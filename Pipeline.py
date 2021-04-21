import sys
import os
sys.path.append(os.environ['AUTOPROF'])
from autoprofutils.Background import Background_Mode, Background_DilatedSources, Background_Unsharp, Background_Basic
from autoprofutils.PSF import PSF_IRAF, PSF_StarFind
from autoprofutils.Center import Center_2DGaussian, Center_1DGaussian, Center_OfMass, Center_HillClimb, Center_Forced, Center_HillClimb_mean
from autoprofutils.Isophote_Initialize import Isophote_Initialize, Isophote_Initialize_mean
from autoprofutils.Isophote_Fit import Isophote_Fit_FFT_Robust, Isophote_Fit_Forced, Photutils_Fit, Isophote_Fit_FFT_mean
from autoprofutils.Mask import Star_Mask_IRAF, Mask_Segmentation_Map
from autoprofutils.Isophote_Extract import Isophote_Extract, Isophote_Extract_Forced
from autoprofutils.Check_Fit import Check_Fit
from autoprofutils.Write_Prof import WriteProf
from autoprofutils.Ellipse_Model import EllipseModel_Fix, EllipseModel_General
from autoprofutils.Radial_Sample import Radial_Sample
from autoprofutils.Orthogonal_Sample import Orthogonal_Sample
from autoprofutils.SharedFunctions import GetOptions, Read_Image, PA_shift_convention
from multiprocessing import Pool, current_process
from astropy.io import fits
from scipy.stats import iqr
from itertools import starmap
from functools import partial
import importlib
import numpy as np
from time import time, sleep
import logging
import warnings
import traceback
from astropy.io.fits.verify import VerifyWarning
warnings.simplefilter('ignore', category=VerifyWarning)

class Isophote_Pipeline(object):

    def __init__(self, loggername = None):
        """
        Initialize pipeline object, user can replace functions with their own if they want, otherwise defaults are used.

        loggername: String to use for logging messages
        """

        # Functions avaiable by default for building the pipeline
        self.pipeline_methods = {'background': Background_Mode,
                                 'background dilatedsources': Background_DilatedSources,
                                 'background unsharp': Background_Unsharp,
                                 'background basic': Background_Basic,
                                 'psf': PSF_StarFind,
                                 'psf IRAF': PSF_IRAF,
                                 'center': Center_HillClimb,
                                 'center mean': Center_HillClimb_mean,
                                 'center forced': Center_Forced,
                                 'center 2DGaussian': Center_2DGaussian,
                                 'center 1DGaussian': Center_1DGaussian,
                                 'center OfMass': Center_OfMass,
                                 'isophoteinit': Isophote_Initialize,
                                 'isophoteinit mean': Isophote_Initialize_mean,
                                 'isophotefit': Isophote_Fit_FFT_Robust,
                                 'isophotefit mean': Isophote_Fit_FFT_mean,
                                 'isophotefit forced': Isophote_Fit_Forced,
                                 'isophotefit photutils': Photutils_Fit,
                                 'starmask': Star_Mask_IRAF,
                                 'mask segmentation map': Mask_Segmentation_Map,
                                 'isophoteextract': Isophote_Extract,
                                 'isophoteextract forced': Isophote_Extract_Forced,
                                 'checkfit': Check_Fit,
                                 'writeprof': WriteProf,
                                 'ellipsemodel': EllipseModel_Fix,
                                 'ellipsemodel general': EllipseModel_General,
                                 'radsample': Radial_Sample,
                                 'orthsample': Orthogonal_Sample}
        
        # Default pipeline analysis order
        self.pipeline_steps = {'head': ['background', 'psf', 'center', 'isophoteinit',
                                        'isophotefit', 'isophoteextract', 'checkfit', 'writeprof']}
        
        # Start the logger
        logging.basicConfig(level=logging.INFO, filename = 'AutoProf.log' if loggername is None else loggername, filemode = 'w')

    def UpdatePipeline(self, new_pipeline_methods = None, new_pipeline_steps = None):
        """
        modify steps in the AutoProf pipeline.

        new_pipeline_methods: update the dictionary of methods used by the pipeline. This can either add
                                new methods or replace existing ones.
        new_pipeline_steps: update the list of pipeline step strings. These strings refer to keys in
                            pipeline_methods. It is posible to add/remove/rearrange steps here. Alternatively
                            one can supply a dictionary with current pipeline steps as keys and new pipeline
                            steps as values, the corresponding steps will be replaced.
        """
        if new_pipeline_methods:
            logging.info('PIPELINE updating these pipeline methods: %s' % str(new_pipeline_methods.keys()))
            self.pipeline_methods.update(new_pipeline_methods)
        if new_pipeline_steps:
            logging.info('PIPELINE new steps: %s' % (str(new_pipeline_steps)))
            if type(new_pipeline_steps) == list:
                self.pipeline_steps['head'] = new_pipeline_steps
            elif type(new_pipeline_steps) == dict:
                assert 'head' in new_pipeline_steps.keys()
                self.pipeline_steps = new_pipeline_steps
            
    def Process_Image(self, options = {}):
        """
        Function which runs the pipeline for a single image. Each sub-function of the pipeline is run
        in order and the outputs are passed along. If multiple images are given, the pipeline is
        excecuted on the first image and the isophotes are applied to the others.

        returns list of times for each pipeline step if successful. else returns 1
        """

        # Seed the random number generator in numpy so each thread gets unique random numbers
        try:
            sleep(0.01)
            np.random.seed(int(np.random.randint(10000)*current_process().pid*(time() % 1) % 2**15))
        except:
            pass
        
        # use filename if no name is given
        if not ('name' in options and type(options['name']) == str):
            startat = options['image_file'].rfind('/') if '/' in options['image_file'] else 0
            options['name'] = options['image_file'][startat: options['image_file'].find('.', startat)]

        # Read the primary image
        try:
            dat = Read_Image(options['image_file'], options)
        except:
            logging.error('%s: could not read image %s' % (options['name'], options['image_file']))
            return 1
            
        # Check that image data exists and is not corrupted
        if dat is None or np.all(dat[int(len(dat)/2.)-10:int(len(dat)/2.)+10, int(len(dat[0])/2.)-10:int(len(dat[0])/2.)+10] == 0):
            logging.error('%s Large chunk of data missing, impossible to process image' % options['name'])
            return 1
        
        # Track time to run analysis
        start = time()
        
        # Run the Pipeline
        timers = {}
        results = {}

        key = 'head'
        step = 0
        while step < len(self.pipeline_steps[key]):
            try:
                logging.info('%s: %s %s at: %.1f sec' % (options['name'], key, self.pipeline_steps[key][step], time() - start))
                print('%s: %s %s at: %.1f sec' % (options['name'], key, self.pipeline_steps[key][step], time() - start))
                if 'branch' in self.pipeline_steps[key][step]:
                    decision = self.pipeline_methods[self.pipeline_steps[key][step]](dat, results, options)
                    if type(decision) == str:
                        key = decision
                        step = 0
                    else:
                        step += 1
                else:
                    step_start = time()
                    dat, res = self.pipeline_methods[self.pipeline_steps[key][step]](dat, results, options)
                    results.update(res)
                    timers[self.pipeline_steps[key][step]] = time() - step_start
                    step += 1
            except Exception as e:
                logging.error('%s: on step %s got error: %s' % (options['name'], self.pipeline_steps[key][step], str(e)))
                logging.error('%s: with full trace: %s' % (options['name'], traceback.format_exc()))
                return 1
            
        print('%s: Processing Complete! (at %.1f sec)' % (options['name'], time() - start))
        logging.info('%s: Processing Complete! (at %.1f sec)' % (options['name'], time() - start))
        return timers
    
    def Process_List(self, options):
        """
        Wrapper function to run "Process_Image" in parallel for many images.
        """

        assert type(options['image_file']) == list
        
        # Format the inputs so that they can be zipped with the images files
        # and passed to the Process_Image function.
        if all(type(v) != list for v in options.values()):
            use_options = [options]*len(options['image_file'])
        else:
            use_options = []
            for i in range(len(options['image_file'])):
                tmp_options = {}
                for k in options.keys():
                    if type(options[k]) == list:
                        tmp_options[k] = options[k][i]
                    else:
                        tmp_options[k] = options[k]
                use_options.append(tmp_options)
        # Track how long it takes to run the analysis
        start = time()
        
        # Create a multiprocessing pool to parallelize image processing
        if options['n_procs'] > 1:
            with Pool(int(options['n_procs'])) as pool:
                res = pool.map(self.Process_Image, use_options,
                               chunksize = 5 if len(options['image_file']) > 100 else 1)
        else:
            res = list(map(self.Process_Image, use_options))
            
        # Report completed processing, and track time used
        logging.info('All Images Finished Processing at %.1f' % (time() - start))
        timers = dict()
        counts = dict()
        for r in res:
            if r == 1:
                continue
            for s in r.keys():
                if s in timers:
                    timers[s] += r[s]
                    counts[s] += 1.
                else:
                    timers[s] = r[s]
                    counts[s] = 1.
        if len(timers) == 0:
            logging.error('All images failed to process!')
            return 
        for s in timers:
            timers[s] /= counts[s]
            logging.info('%s took %.3f seconds on average' % (s, timers[s]))
        
        # Return the success/fail indicators for every Process_Image excecution
        return res
        
    def Process_ConfigFile(self, config_file):
        """
        Reads in a configuration file and sets parameters for the pipeline. The configuration
        file should have variables corresponding to the desired parameters to be set.

        congig_file: string path to configuration file

        returns: timing of each pipeline step if successful. Else returns 1
        """
        
        # Import the config file regardless of where it is from
        if '/' in config_file:
            startat = config_file.rfind('/')+1
        else:
            startat = 0
        if '.' in config_file:
            use_config = config_file[startat:config_file.find('.', startat)]
        else:
            use_config = config_file[startat:]
        if '/' in config_file:
            sys.path.append(config_file[:config_file.rfind('/')])
        try:
            c = importlib.import_module(use_config)
        except:
            sys.path.append(os.getcwd())
            c = importlib.import_module(use_config)

        if 'forced' in c.process_mode:
            self.UpdatePipeline(new_pipeline_steps = ['background', 'psf', 'center forced', 'isophoteinit',
                                                      'isophotefit forced', 'isophoteextract forced', 'writeprof'])
            
        try:
            self.UpdatePipeline(new_pipeline_methods = c.new_pipeline_methods)
        except:
            pass
        try:
            self.UpdatePipeline(new_pipeline_steps = c.new_pipeline_steps)
        except:
            pass
            
        use_options = GetOptions(c)
            
        if c.process_mode in ['image', 'forced image']:
            return self.Process_Image(use_options)
        elif c.process_mode in ['image list', 'forced image list']:
            return self.Process_List(use_options)
        else:
            logging.error('Unrecognized process_mode! Should be in: [image, image list, forced image, forced image list]')
            return 1
        

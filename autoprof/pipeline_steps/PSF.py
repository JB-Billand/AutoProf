from photutils import DAOStarFinder, IRAFStarFinder
import numpy as np
from scipy.stats import iqr
import matplotlib.pyplot as plt
from astropy.visualization import SqrtStretch, LogStretch
from astropy.visualization.mpl_normalize import ImageNormalize
from matplotlib.patches import Ellipse
import logging
from astropy.io import fits
from itertools import product
from scipy.optimize import minimize
from scipy.stats import norm, multivariate_normal
from scipy.fftpack import fft, ifft
import sys
import os
sys.path.append(os.environ['AUTOPROF'])
from autoprofutils.SharedFunctions import StarFind, AddLogo, LSBImage, autocolours, interpolate_Lanczos, interpolate_bicubic
from autoprofutils.Diagnostic_Plots import Plot_PSF_Stars
from copy import deepcopy

def PSF_IRAF(IMG, results, options):
    """PSF routine which identifies stars and averages the FWHM.

    Uses the photutil IRAF wrapper to identify stars in the image and
    computes the average FWHM.

    Arguments
    -----------------
    
    ap_guess_psf: float
      Initialization value for the PSF calculation in pixels. If not
      given, AutoProf will default with a guess of 1/*ap_pixscale*

      :default:
        None, use 1 arcsec

    ap_set_psf: float
      force AutoProf to use this PSF value (in pixels) instead of
      calculating its own.

      :default:
        None    

    References
    ----------
    - 'background'
    - 'background noise'
    
    Returns
    -------
    IMG: ndarray
      Unaltered galaxy image
    
    results: dict
      .. code-block:: python
    
        {'psf fwhm':  # FWHM of the average PSF for the image
        }

    """
    if 'ap_set_psf' in options:
        logging.info('%s: PSF set by user: %.4e' % (options['ap_name'], options['ap_set_psf']))
        return IMG, {'psf fwhm': options['ap_set_psf']}
    elif 'ap_guess_psf' in options:
        logging.info('%s: PSF initialized by user: %.4e' % (options['ap_name'], options['ap_guess_psf']))
        fwhm_guess = options['ap_guess_psf']
    else:
        fwhm_guess = max(1., 1./options['ap_pixscale'])

    edge_mask = np.zeros(IMG.shape, dtype = bool)
    edge_mask[int(IMG.shape[0]/5.):int(4.*IMG.shape[0]/5.),
              int(IMG.shape[1]/5.):int(4.*IMG.shape[1]/5.)] = True
    
    dat = IMG - results['background']
    # photutils wrapper for IRAF star finder
    count = 0
    sources = 0
    psf_iter = deepcopy(psf_guess)
    try:
        while count < 5 and sources < 20:
            iraffind = IRAFStarFinder(fwhm = psf_iter, threshold = 6.*results['background noise'], brightest = 50)
            irafsources = iraffind.find_stars(dat, edge_mask)
            psf_iter = np.median(irafsources['fwhm'])
            if np.median(irafsources['sharpness']) >= 0.95:
                break
            count += 1
            sources = len(irafsources['fwhm'])
    except:
        return IMG, {'psf fwhm': fwhm_guess}
    if len(irafsources) < 5:
        return IMG, {'psf fwhm': fwhm_guess}
    
    psf = np.median(irafsources['fwhm'])
    
    if 'ap_doplot' in options and options['ap_doplot']:
        Plot_PSF_Stars(IMG, irafsources['xcentroid'], irafsources['ycentroid'], irafsources['fwhm'], psf, results, options)

    return IMG, {'psf fwhm': psf, 'auxfile psf': 'psf fwhm: %.3f pix' % psf}

def PSF_StarFind(IMG, results, options):
    """PSF routine which identifies stars and averages the FWHM.

    The PSF method uses an edge finding convolution filter to identify
    candidate star pixels, then averages their FWHM. Randomly iterates
    through the pixels and searches for a local maximum. An FFT is
    used to identify non-circular star candidate (artifacts or
    galaxies) which may have been picked up by the edge
    finder. Circular apertures are placed around the star until half
    the central flux value is reached, This is recorded as the FWHM
    for that star. A collection of 50 stars are identified and the
    most circular (by FFT coefficients) half are kept, a median is
    taken as the image PSF.

    Arguments
    -----------------
    
    ap_guess_psf: float
      Initialization value for the PSF calculation in pixels. If not
      given, AutoProf will default with a guess of 1/*ap_pixscale*

      :default:
        None, use 1 arcsec

    ap_set_psf: float
      force AutoProf to use this PSF value (in pixels) instead of
      calculating its own.

      :default:
        None

    References
    ----------
    - 'background'
    - 'background noise'    
    
    Returns
    -------
    IMG: ndarray
      Unaltered galaxy image
    
    results: dict
      .. code-block:: python
    
        {'psf fwhm':  # FWHM of the average PSF for the image
        }

    """

    if 'ap_set_psf' in options:
        logging.info('%s: PSF set by user: %.4e' % (options['ap_name'], options['ap_set_psf']))
        return IMG, {'psf fwhm': options['ap_set_psf']}
    elif 'ap_guess_psf' in options:
        logging.info('%s: PSF initialized by user: %.4e' % (options['ap_name'], options['ap_guess_psf']))
        fwhm_guess = options['ap_guess_psf']
    else:
        fwhm_guess = max(1., 1./options['ap_pixscale'])

    edge_mask = np.zeros(IMG.shape, dtype = bool)
    edge_mask[int(IMG.shape[0]/5.):int(4.*IMG.shape[0]/5.),
              int(IMG.shape[1]/5.):int(4.*IMG.shape[1]/5.)] = True
    stars = StarFind(IMG - results['background'], fwhm_guess, results['background noise'],
                     edge_mask,  maxstars = 50)
    if len(stars['fwhm']) <= 10:
        logging.error('%s: unable to detect enough stars! PSF results not valid, using 1 arcsec estimate psf of %f' % (options['ap_name'], fwhm_guess))
        return IMG, {'psf fwhm': fwhm_guess}
    
    def_clip = 0.1
    while np.sum(stars['deformity'] < def_clip) < max(10,len(stars['fwhm'])/2):
        def_clip += 0.1
    psf = np.median(stars['fwhm'][stars['deformity'] < def_clip])
    if 'ap_doplot' in options and options['ap_doplot']:
        Plot_PSF_Stars(IMG, stars['x'], stars['y'], stars['fwhm'], psf, results, options, flagstars = stars['deformity'] >= def_clip)

    logging.info('%s: found psf: %f with deformity clip of: %f' % (options['ap_name'],psf, def_clip))
    return IMG, {'psf fwhm': psf, 'auxfile psf': 'psf fwhm: %.3f pix' % psf}

def PSF_Image(IMG, results, options):
    """

    """
    if 'ap_set_psf' in options:
        logging.info('%s: PSF set by user: %.4e' % (options['ap_name'], options['ap_set_psf']))
        return IMG, {'psf fwhm': options['ap_set_psf']}
    elif 'ap_guess_psf' in options:
        logging.info('%s: PSF initialized by user: %.4e' % (options['ap_name'], options['ap_guess_psf']))
        fwhm_guess = options['ap_guess_psf']
    else:
        fwhm_guess = max(1., 1./options['ap_pixscale'])
    
    edge_mask = np.zeros(IMG.shape, dtype = bool)
    edge_mask[int(IMG.shape[0]/5.):int(4.*IMG.shape[0]/5.),
              int(IMG.shape[1]/5.):int(4.*IMG.shape[1]/5.)] = True
    dat = IMG - results['background']
    stars = StarFind(dat, fwhm_guess, results['background noise'],
                     edge_mask, detect_threshold = 5.)
    if len(stars['fwhm']) <= 10:
        logging.error('%s: unable to detect enough stars! PSF results not valid, using 1 arcsec estimate psf of %f' % (options['ap_name'], fwhm_guess))

    def_clip = 0.1
    while np.sum(stars['deformity'] < def_clip) < max(10,len(stars['fwhm'])*2/3):
        def_clip += 0.1
    psf = np.median(stars['fwhm'][stars['deformity'] < def_clip])
    psf_iqr = np.quantile(stars['fwhm'][stars['deformity'] < def_clip], [0.1,0.9])
    print(psf, psf_iqr)
    psf_size = int(psf*20)
    if psf_size % 2 == 0: # make PSF odd for easier calculations
        psf_size += 1
    print(psf_size)
    psf_img = None
    XX, YY = np.meshgrid(np.array(range(psf_size)) - psf_size//2, np.array(range(psf_size)) - psf_size//2)
    XX, YY = np.ravel(XX), np.ravel(YY)
    for i in range(len(stars['x'])):
        if stars['deformity'][i] > def_clip or stars['fwhm'][i] < psf_iqr[0] or stars['fwhm'][i] > psf_iqr[1]:
            continue
        if stars['x'][i] < psf_size//2 or (dat.shape[1] - stars['x'][i]) < psf_size//2 or stars['y'][i] < psf_size//2 or (dat.shape[1] - stars['y'][i]) < psf_size//2:
            continue
        print(i)
        flux = interpolate_Lanczos(dat, XX + stars['x'][i], YY + stars['y'][i], 10).reshape((1,psf_size, psf_size))
        plt.imshow(dat[int(stars['y'][i] - psf_size/2):int(stars['y'][i] + psf_size/2),
                       int(stars['x'][i] - psf_size/2):int(stars['x'][i] + psf_size/2)], origin = 'lower')
        plt.savefig('plots/psf_%i_dat.jpg' % i)
        plt.close()
        plt.imshow(flux[0], origin = 'lower')
        plt.savefig('plots/psf_%i_flux.jpg' % i)
        plt.close()
        flux /= np.sum(flux)
        if psf_img is None:
            psf_img = flux
        else:
            psf_img = np.concatenate((psf_img, flux))
    psf_img = np.median(psf_img, axis = 0)
    psf_img /= np.sum(psf_img)
    
    header = fits.Header()
    hdul = fits.HDUList([fits.PrimaryHDU(header=header),
                         fits.ImageHDU(psf_img)])
    
    hdul.writeto(os.path.join(options['ap_saveto'] if 'ap_saveto' in options else '', '%s_psf.fits' % options['ap_name']), overwrite = True)    
    
    return IMG, {'psf fwhm': psf, 'auxfile psf': 'psf fwhm: %.3f pix' % psf, 'psf img': psf_img}

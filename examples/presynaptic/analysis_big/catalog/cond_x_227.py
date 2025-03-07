"""
Segmentation and analysis meta-data
"""


#######################################################
#
# Experiment description and file names
#

# identifier
identifier = 'cond_x_227'

# experimental condition
treatment = 'cond_x'

# batch and original identifier
batch = 'XY_2'

ori_identifier = 'tomo27_XY_2_x'

# synaptic vesicles
sv_file = '../../segmentation/XY_2/tomo27_XY_2_x/vesicles/tomo27_XY_2_x_vesicles.pkl'
sv_membrane_file = '../../segmentation/XY_2/tomo27_XY_2_x/vesicles/tomo27_XY_2_x_mem.pkl'
sv_lumen_file = '../../segmentation/XY_2/tomo27_XY_2_x/vesicles/tomo27_XY_2_x_lum.pkl'

# hierarchical segmentation of tethers and connectors
tethers_file = '../../segmentation/XY_2/tomo27_XY_2_x/connectors/tomo27_XY_2_x_new_AZ_good.pkl'
connectors_file = '../../segmentation/XY_2/tomo27_XY_2_x/connectors/tomo27_XY_2_x_new_rest_good.pkl'

# layers
layers_file =  '../../segmentation/XY_2/tomo27_XY_2_x/layers/new_labels_layers.dat'


########################################################
#
# Observations
#

# mitochondria in the presyn terminal
mitochondria = True


######################################################
#
# Microscopy
#

# microscope
microscope = 'Titan_2'

# pixel size
pixel_size = 1.756

# person who recorded the series
operator = 'someone'

# person who did membrane segmentation
segmented = 'someone'

#DDD or CCD
detector = 'DDD'

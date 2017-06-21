# coding=utf-8


# Location of local layer file that can be accessed by InaSAFE worker directly
# This path will be accessed by InaSAFE Celery Worker
INASAFE_LAYER_DIRECTORY = '/home/geosafe/layers/'

# Location of InaSAFE Impact Layer output that can be accessed by
# GeoSAFE.
# This path will be accessed by GeoSAFE
GEOSAFE_IMPACT_OUTPUT_DIRECTORY = '/home/geosafe/impact_layers/'


# Location of InaSAFE Impact Layer output base url
# This base url will be set at root path for GEOSAFE_IMPACT_OUTPUT_DIRECTORY
INASAFE_IMPACT_BASE_URL = '/output'


# This base url is needed for InaSAFE worker to be able to find Geonode
GEONODE_BASE_URL = 'http://localhost:8000/'

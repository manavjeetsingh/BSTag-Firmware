from scripts.hardware_api.hardware import *
from scripts.ref_functions.spec_functions import *
from scripts.ref_functions.util_functions import *
import time
import numpy as np
from matplotlib import pyplot as plt

tag1_mac = b'EC:62:60:4D:34:8C\r\n'
tag2_mac = b'94:3C:C6:6D:53:5C\r\n'
tag3_mac = b'10:97:BD:D4:05:10\r\n'
tag4_mac = b'94:3C:C6:6D:29:2C\r\n'

tag1_com = "COM9"
tag2_com = "COM10"
tag3_com = "COM4"
tag4_com = "COM5"

t1 = time.time()

tag1 = Tag(tag1_com)
tag1.get_adc_val()

# tag2 = Tag(tag2_com)
# tag2.reflect(b'ch_2\0\n')
#
#
# act_mac1 = tag1.get_mac()
# act_mac2 = tag2.get_mac()
#
#
# assert(act_mac1 == tag1_mac)
# assert(act_mac2 == tag2_mac)
# this code collects power vs voltage data collection

from ribbn_scripts.hardware_api.hardware import *
from ribbn_scripts.ref_functions.util_functions import *
import time

exc = Exciter()
exc.set_freq(915)
exc.set_pwr(-30)

fn = 'v32-3'
tag = Tag("COM6")
tag.reflect(b'ch_2\0\n')

pwr_range = range(-35, -13, 1)
tim_delay = 0.5
col_data = {}

for freq in range(775,1010,10):
    exc.set_freq(freq)
    one_freq_data = []

    for pwr in pwr_range:
        exc.set_pwr(pwr)
        time.sleep(tim_delay)
        v = np.median(tag.get_adc_val())
        one_freq_data.append(v)
        print(v)
    exc.set_pwr(-30)

    col_data[freq] = l2a(one_freq_data)

# write_pickle('D:/git/T2TExperiments/coba/calibrations/PV_data_Aug2024/'+fn+'_pv_dat.pkl', col_data)
# write_pickle('C:/git/T2TExperiments/coba/calibrations/PV_data_Aug2024/'+fn+'_pv_dat.pkl', col_data)
write_pickle('C:/git/T2TExperiments/coba/calibrations/PV_data_Dec2025/'+fn+'_pv_dat.pkl', col_data)
plt.plot(pwr_range, col_data[915], 'o')
plt.show()

# this code collects power vs voltage data collection

from ribbn_scripts.hardware_api.hardware import *
from ribbn_scripts.ref_functions.util_functions import *
import time

exc = Exciter()
exc.set_freq(915)
exc.set_pwr(-30)

save_folder='PV_data_Sept2026_705_995_-7'


tag = Tag("COM31")
fn=tag.get_mac().replace(":",'_')
tag.reflect(2)

pwr_range = range(-35, -7, 1)
tim_delay = 0.1
col_data = {}

for freq in range(705,1000,10):
    exc.set_freq(freq)
    one_freq_data = []

    for pwr in pwr_range:
        exc.set_pwr(pwr)
        time.sleep(tim_delay)
        if pwr==-35:
            print("extra_sleep")
            time.sleep(0.5)
        v = np.median(tag.get_adc_val())
        one_freq_data.append(v)
        print(v)
    exc.set_pwr(-30)

    col_data[freq] = l2a(one_freq_data)

# write_pickle('D:/git/T2TExperiments/coba/calibrations/PV_data_Aug2024/'+fn+'_pv_dat.pkl', col_data)
# write_pickle('C:/git/T2TExperiments/coba/calibrations/PV_data_Aug2024/'+fn+'_pv_dat.pkl', col_data)
write_pickle(save_folder+'/'+fn+'_pv_dat.pkl', col_data)
# plt.plot(pwr_range, col_data[915], 'o')
plt.show()

from ribbn_scripts.ref_functions.util_functions import *
from matplotlib import pyplot as plt
import numpy as np
from scipy import signal as sig

fn = 'v32-3'
col_data = read_pickle('C:/git/T2TExperiments/coba/calibrations/PV_data_Dec2025/'+fn+'_pv_dat.pkl')
# print(col_data)
pwr_range = range(-35, -13, 1)
plt.figure()
pv_polynomials = {}
ctr = 1
for freq in range(775,1010,10):
# for freq in [915]:
# for freq in [775, 915,995]:
    # plt.title(freq)
    x = col_data[freq][0:len(pwr_range)]
    y = np.array(pwr_range)

    # print(x)
    p = np.polyfit(np.log(x), y, 2)
    p_inv = np.polyfit(y, np.log(x), 2)
    poly_dict = {'polynomial': p, 'inverse': p_inv}
    pv_polynomials[freq] = poly_dict

    # plt.subplot(7, 4, ctr)
    plt.plot(x, y, '.')
    # print(y)
    # print(x)
    beautify_graph(True, 'voltage (mV)', 'pwr (dbm)', 'frequency')
    #
    xnew = np.linspace(min(x), max(x), 100)
    target_mV=15
    print(f"At frequency: {freq} Mhz, {np.polyval(p, np.log(target_mV))} power is required to achieve {target_mV} mV out of ADC.")
    plt.plot(xnew, np.polyval(p, np.log(xnew)),label=str(freq))
    plt.legend()
    ctr = ctr + 1

# beautify_graph(True, 'voltage (mV)', 'pwr (dbm)', 'frequency')
# plt.savefig('../../plots/calibration_images/pv/rx.pdf')
plt.xlabel('mV')
plt.ylabel('dbm')
plt.show()

write_pickle('C:/git/T2TExperiments/coba/calibrations/PV_data_Dec2025/'+fn+'_pv_polynomials_rx.pkl', pv_polynomials)


# freq=915
# x = col_data[freq]
# y = np.array(pwr_range)
# # p = np.polyfit(np.log(x), y, 2)
# # p_inv = np.polyfit(y, np.log(x), 3)
#
# # plt.subplot(7, 4, ctr)
# plt.plot(x, y, 'o')
#
# xnew = np.linspace(min(x), max(x), 100)
# ynew = np.linspace(min(y), max(y), 100)
# # plt.plot(xnew, np.polyval(p, np.log(xnew)))
# # plt.plot(np.exp(np.polyval(p_inv, ynew)), ynew)
#
#
#
# plt.show()

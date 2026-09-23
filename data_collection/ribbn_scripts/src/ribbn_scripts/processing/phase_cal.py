import numpy as np
import math

def cal_theta_et_al(adcs, rxName, txName, cfg, freq):
    """freq in hz"""
    amp = []
    phi = []
    attn = []
    for channel in adcs.keys():
        dbm = np.polyval(cfg['pv'][rxName][freq]['polynomial'], np.log(adcs[channel]))
        uW = np.power(10, (dbm - 30) / 10) * 1e6
        amp.append(np.sqrt(uW * 50 * 2))
        # pwr = int(round(dbm, 0))
        
        # despite the _s11_poly file name, these hold the raw VNA sweep
        # (freq axis + per-point values), not polynomial coefficients
        s11 = cfg['s11'][txName][channel]
        phi.append(np.interp(freq, s11['freq'], np.unwrap(s11['phase'])))
        attn.append(np.interp(freq, s11['freq'], s11['amp']))

    h = []
    for a, p in zip(attn, phi):
        h.append([1, a * np.cos(p), a * np.sin(p)])
    # print("****************************************************")
    # print(h)
    out = np.matmul(np.matmul(np.linalg.inv(np.matmul(np.transpose(h), h)), np.transpose(h)), amp)
    theta_rad = math.atan2(out[2], out[1])
    theta_deg = np.rad2deg(math.atan2(out[2], out[1]))
    V = out[0]
    beta = math.sqrt(out[1] * out[1] + out[2] * out[2]) 
    
    return theta_deg, V, beta

import numpy as np
import math

def cal_theta(adcs, rxName, txName, cfg):
    amp = []
    phi = []
    attn = []
    for channel in adcs.keys():
        dbm = np.polyval(cfg['pv'][rxName], np.log(adcs[channel]))
        uW = np.power(10, (dbm - 30) / 10) * 1e6
        amp.append(np.sqrt(uW * 50 * 2))
        pwr = int(round(dbm, 0))
        if pwr < -30:
            pwr = -30
        elif pwr > -12:
            pwr = -12
        phi.append(np.polyval(cfg['s11'][txName][f'{channel};{pwr}'][0], 915))
        attn.append(np.polyval(cfg['s11'][txName][f'{channel};{pwr}'][1], 915))

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
    
    return theta_deg

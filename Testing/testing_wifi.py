import serial
import numpy as np
import time
from matplotlib import pyplot as plt
from ribbn_scripts.hardware_api.hardware import Exciter,Tag
import json

com_port1="/dev/tty.usbmodem2113301"
tag1=Tag(com_port1)
ip_port_out = tag1.get_ip_port()
print(ip_port_out)
if ip_port_out['net']=='up':
    tag1.connect_wifi(ip_port_out["ip"], ip_port_out["port"])
else:
    raise Exception("Net not connected.")


com_port2="/dev/tty.usbmodem2113401"
tag2=Tag(com_port2)
ip_port_out = tag2.get_ip_port()
print(ip_port_out)
if ip_port_out['net']=='up':
    tag2.connect_wifi(ip_port_out["ip"], ip_port_out["port"])
else:
    raise Exception("Net not connected.")

def find_periodic_windows(voltage_readings, period=90, top_k=10):
    readings = np.asarray(voltage_readings, dtype=float)
    num_windows = len(readings) - period + 1
    if num_windows < top_k:
        raise ValueError(f"Not enough samples ({len(readings)}) for period={period} and top_k={top_k}.")

    windows = np.lib.stride_tricks.sliding_window_view(readings, period)
    centered = windows - windows.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(centered, axis=1)
    norms[norms == 0] = 1e-12
    normalized = centered / norms[:, None]

    corr_matrix = normalized @ normalized.T
    np.fill_diagonal(corr_matrix, -np.inf)

    best_match_scores = np.sort(corr_matrix, axis=1)[:, -(top_k - 1):].sum(axis=1)
    candidate_indices = np.argsort(best_match_scores)[::-1]

    selected_indices = []
    for idx in candidate_indices:
        if all(abs(idx - s) >= period for s in selected_indices):
            selected_indices.append(idx)
        if len(selected_indices) == top_k:
            break

    return [
        {
            "start_index": int(idx),
            "score": float(best_match_scores[idx]),
            "window": windows[idx].copy(),
        }
        for idx in selected_indices
    ]

def fit_edge_grid(voltage_readings, period=100, channels=6, min_spacing=None):
    """
    Detect channel-switch edges in a periodic square-wave-like trace and
    jointly fit a period/phase grid to them via iterative least squares.

    Channel switches produce a big sample-to-sample jump; in-plateau noise
    doesn't. We threshold |diff| to get candidate edges, assign each one an
    integer "which channel switch is this" index k assuming edges are spaced
    ~period/channels apart, then do a linear fit of edge_index = phase + k *
    sub_period. Re-round k against the fitted line and refit until it's
    stable (edges detected off by one k are pulled onto the correct line).

    Returns a dict with the fitted sub_period/period/phase and a `grid`
    array of every predicted edge position across the full input, so the
    first entry of `grid` is exactly the "first blue line" position.
    """
    readings = np.asarray(voltage_readings, dtype=float)
    abs_diff = np.abs(np.diff(readings))

    sub_period = period / channels
    if min_spacing is None:
        min_spacing = sub_period / 2

    threshold = (abs_diff.max() + np.median(abs_diff)) / 2
    candidates = np.where(abs_diff > threshold)[0]
    if len(candidates) == 0:
        raise ValueError("No edges found above threshold; signal may not be a clean square wave.")

    edges = []
    for idx in candidates:
        if not edges or idx - edges[-1] >= min_spacing:
            edges.append(idx)
        elif abs_diff[idx] > abs_diff[edges[-1]]:
            edges[-1] = idx
    edges = np.asarray(edges, dtype=float) + 0.5  # diff[i] sits between sample i and i+1

    if len(edges) < channels:
        raise ValueError(f"Only found {len(edges)} edges; need at least {channels} to fit a grid.")

    k = np.round((edges - edges[0]) / sub_period)
    for _ in range(10):
        A = np.vstack([k, np.ones_like(k)]).T
        sub_period_fit, phase_fit = np.linalg.lstsq(A, edges, rcond=None)[0]
        new_k = np.round((edges - phase_fit) / sub_period_fit)
        if np.array_equal(new_k, k):
            break
        k = new_k

    phase = phase_fit % sub_period_fit
    n_grid = int(np.ceil((len(readings) - phase) / sub_period_fit))
    grid = phase + np.arange(n_grid) * sub_period_fit

    return {
        "sub_period": sub_period_fit,
        "period": sub_period_fit * channels,
        "phase": phase,
        "edge_indices": edges,
        "grid": grid,
    }


def plot_periodic_windows(results, grid=None, vline_interval=None):
    k = len(results)
    cols = min(5, k)
    rows = int(np.ceil(k / cols))
    plt.figure(figsize=(4 * cols, 3 * rows))
    for i, r in enumerate(results):
        plt.subplot(rows, cols, i + 1)
        plt.plot(r["window"], '.-')
        if grid is not None:
            start = r["start_index"]
            end = start + len(r["window"])
            for x in grid[(grid >= start) & (grid < end)]:
                plt.axvline(x=x - start, color='b')
        elif vline_interval:
            for x in range(0, len(r["window"]), vline_interval):
                plt.axvline(x=x, color='b')
        plt.title(f"start={r['start_index']}, score={r['score']:.2f}")
        plt.xlabel("Sample")
        plt.ylabel("ADC out [mV]")
    plt.tight_layout()
    plt.show()

def plotMPPs_wifi(t1, t2, passes=1):
    per_channel_time=0.001
    plt.figure(figsize=(20,10))
    voltages=[]
    for rep in range(5):
        plt.subplot(2,3,rep+1)
        print("Begin reading")     
        t1.begin_reading_wifi()

        print("Starting MPP")     
        mpp_start_time,mpp_stop_time=t2.perform_mpp_wifi(passes=passes)

        print("Reading response")
        voltage_readings=t1.stop_reading_wifi()
        print(f"Got {len(voltage_readings)} readings.")

        mpp_time_elapsed=mpp_stop_time-mpp_start_time
        plot_all_time=np.arange(0,mpp_time_elapsed,mpp_time_elapsed/len(voltage_readings))
        plot_end_time=plot_all_time[-1]
        pass_duration=per_channel_time*6
        ver_lines=[]
        for p in range(passes):
            pass_end_time=plot_end_time-pass_duration*p
            for i in range(6):
                ver_lines.append(pass_end_time-per_channel_time*(i+1))

        # for v in ver_lines:
        #     plt.axvline(x = v, color = 'b', label = 'axvline - full height')

        plt.plot(np.arange(0,mpp_time_elapsed,mpp_time_elapsed/len(voltage_readings))[:len(voltage_readings)],voltage_readings,'.-')
        voltages.append(voltage_readings)
        plt.xlabel("Time [s]")
        plt.ylabel("ADC out [mV]")
    plt.show()

    return voltages

def plotMPPs(t1, t2, passes=1):
    per_channel_time=0.001
    plt.figure(figsize=(20,10))
    voltages=[]
    for rep in range(5):
        plt.subplot(2,3,rep+1)
        print("Begin reading")     
        t1.begin_reading()

        print("Starting MPP")     
        mpp_start_time,mpp_stop_time=t2.perform_mpp(passes=passes)

        print("Reading response")
        voltage_readings=t1.stop_reading()
        print(f"Got {len(voltage_readings)} readings.")

        mpp_time_elapsed=mpp_stop_time-mpp_start_time
        plot_all_time=np.arange(0,mpp_time_elapsed,mpp_time_elapsed/len(voltage_readings))
        plot_end_time=plot_all_time[-1]
        pass_duration=per_channel_time*6
        ver_lines=[]
        for p in range(passes):
            pass_end_time=plot_end_time-pass_duration*p
            for i in range(6):
                ver_lines.append(pass_end_time-per_channel_time*(i+1))

        # for v in ver_lines:
        #     plt.axvline(x = v, color = 'b', label = 'axvline - full height')

        plt.plot(np.arange(0,mpp_time_elapsed,mpp_time_elapsed/len(voltage_readings))[:len(voltage_readings)],voltage_readings, '.-')
        voltages.append(voltage_readings)
        plt.xlabel("Time [s]")
        plt.ylabel("ADC out [mV]")
    plt.show()
    
    return voltages



# tag1.begin_reading_wifi()
# time.sleep(0.2)
# voltage_readings_newtag1_wifi=tag1.stop_reading_wifi()
# print("Voltage array len:",len(voltage_readings_newtag1_wifi))
# plt.plot(voltage_readings_newtag1_wifi, '.', markersize=1)
# plt.xlabel("#")
# plt.ylabel("ADC out (mV)")
# plt.show()


voltages=plotMPPs_wifi(t1=tag1, t2=tag2, passes=10)
# voltages=plotMPPs(t1=tag1, t2=tag2, passes=10)
top_k=find_periodic_windows(voltages[4], period=100, top_k=10)
edge_grid=fit_edge_grid(voltages[4], period=100, channels=6)
print(f"First edge (first blue line) at sample {edge_grid['grid'][0]:.2f}, "
      f"fitted period={edge_grid['period']:.2f}")
plot_periodic_windows(top_k, grid=edge_grid["grid"])

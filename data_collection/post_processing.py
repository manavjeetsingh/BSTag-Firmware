"""Post-process MPP testing CSVs into per-frequency box plots.

Two plots are produced per CSV:

1. Combined link phase. For every (link, frequency, run exp num) triple, the
   two unidirectional phase measurements (Rx=A,Tx=B and Rx=B,Tx=A) are
   combined as:

       combined_phase = ((phase_dir1 + phase_dir2) / 2) mod 180   # deg, i.e. mod pi

   The combined phase varies across "Run Exp Num" for a fixed (link,
   frequency), so each link/frequency pair gets a box across runs. Each link
   gets its own subplot in a shared-frequency-axis stack, written as a PDF.

   If --distance is given and the CSV has exactly two unique tags, the
   theoretical phase is overlaid in gray:

       theta_ab = (2 * pi * f * d_ab / c) mod pi

   NOTE: fitting this against measured combined phase on 775-995MHz data
   landed on an effective distance roughly 2x the physically-measured one.
   That gap is unresolved -- it's degenerate with a fixed reference-plane/
   calibration length error in the S11 data (both explain the observed
   slope equally well from a single distance point), so don't read the
   overlay as validated; it's the formula as given, not a confirmed match.

2. Average voltage irrespective of channel. For every row, the six
   Channel_*_median columns are averaged into one per-row voltage, then
   boxed per (Rx->Tx direction, frequency) across run exp numbers -- one
   subplot per direction, written as a PDF.

3. Voltage traces (PDF). One subplot per row for the first --runs run exp
   numbers, showing the complete captured trace against time with the channel
   segment boundaries and per-segment medians drawn on top. The segmentation
   mirrors getChannelVoltage() in measurePhasesMultiThreadedMultiTags.py.
   Pass --freq to restrict this PDF to a single frequency's rows.

Usage:
    python post_processing.py <csv_path> [--out-dir DIR] [--runs N] [--freq MHZ] [--distance METERS]
"""

import argparse
import ast
import re
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import pandas as pd

from ribbn_scripts.processing.mpp_segment import segment_capture, channel_windows

# dataviz categorical palette (references/palette.md), light-mode steps
CATEGORICAL_COLORS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]

PHASE_COL = "Unidirectional Phase (deg)"
FREQ_COL = "Frequency (MHz)"
RUN_COL = "Run Exp Num"
REP_COL = "MPP Repetition"

SPEED_OF_LIGHT = 299792458.0  # m/s


def theoretical_phase_deg(freq_mhz, distance_m: float) -> np.ndarray:
    """theta_ab = (2*pi*f*d_ab / c) mod pi, in degrees to match the mod-180 combined phase."""
    freq_hz = np.asarray(freq_mhz, dtype=float) * 1e6
    return np.degrees((2 * np.pi * freq_hz * distance_m / SPEED_OF_LIGHT) % np.pi)


def load_combined_phases(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, usecols=["Rx", "Tx", FREQ_COL, RUN_COL, REP_COL, PHASE_COL])
    df["series"] = df.apply(lambda r: "-".join(sorted([r["Rx"], r["Tx"]])), axis=1)

    rows = []
    # a repetition is its own pair of directions - grouping without it lumps all
    # MPP_REPETITIONS together and nothing has exactly two measurements
    for (series, freq, run, rep), g in df.groupby(["series", FREQ_COL, RUN_COL, REP_COL]):
        if len(g) != 2:
            print(
                f"warning: expected 2 unidirectional measurements for "
                f"link={series} freq={freq} run={run} rep={rep}, got {len(g)}; skipping",
                file=sys.stderr,
            )
            continue
        combined = ((g[PHASE_COL].iloc[0] + g[PHASE_COL].iloc[1]) / 2) % 180.0
        rows.append({"series": series, "frequency": freq, "run": run, "rep": rep, "value": combined})

    return pd.DataFrame(rows)


def load_avg_voltage(csv_path: str) -> pd.DataFrame:
    header = pd.read_csv(csv_path, nrows=0).columns
    median_cols = [c for c in header if c.startswith("Channel_") and c.endswith("_median")]

    df = pd.read_csv(csv_path, usecols=["Rx", "Tx", FREQ_COL, RUN_COL, *median_cols])
    df["series"] = df["Rx"] + " -> " + df["Tx"]
    df["value"] = df[median_cols].mean(axis=1)

    return df[["series", FREQ_COL, RUN_COL, "value"]].rename(columns={FREQ_COL: "frequency", RUN_COL: "run"})


def parse_float_array(text: str) -> np.ndarray:
    """Parse a stored array cell, written either as a Python list or a numpy repr."""
    if "..." in text:
        raise ValueError(
            "array was written as numpy's summarised repr and is missing samples; "
            "this CSV predates the .tolist() fix in measurePhasesMultiThreadedMultiTags.py"
        )
    try:
        return np.asarray(ast.literal_eval(text), dtype=float)
    except (ValueError, SyntaxError):
        return np.asarray([float(v) for v in re.split(r"[\s,]+", text.strip("[] \n"))])


def segment_trace(voltages: np.ndarray, elapsed: float, channels: list):
    """Locate the channel dwells, using the same fit as the live pipeline.

    Returns a time axis for the complete trace, the boundary positions and
    (channel, left, right, median) per segment -- all in seconds, so `elapsed`
    is used only to scale the axis for display, never to place the boundaries.
    """
    medians, per_channel, (start, dwell, _) = segment_capture(voltages, channels)

    dt = elapsed / len(voltages)
    time = np.arange(len(voltages)) * dt

    ver_lines = [(start + i * dwell) * dt for i in range(len(channels) + 1)]
    segments = [
        (ch, lo * dt, hi * dt, medians[ch])
        for ch, (lo, hi) in channel_windows(start, dwell, channels).items()
    ]
    return time, ver_lines, segments


def plot_voltage_traces(csv_path: str, n_runs: int, output_path: str, freq: float = None) -> None:
    df = pd.read_csv(csv_path)
    channels = sorted(
        int(c.split("_")[1]) for c in df.columns if c.startswith("Channel_") and c.endswith("_median")
    )

    if freq is not None:
        df = df[np.isclose(df[FREQ_COL], freq)]
        if df.empty:
            print(f"no rows found for frequency {freq} MHz", file=sys.stderr)
            return

    runs = sorted(df[RUN_COL].unique())[:n_runs]
    rows = df[df[RUN_COL].isin(runs)]
    if rows.empty:
        print("no rows to plot for the requested runs", file=sys.stderr)
        return

    per_page = 6
    with PdfPages(output_path) as pdf:
        for page_start in range(0, len(rows), per_page):
            page = rows.iloc[page_start:page_start + per_page]
            nrows = -(-len(page) // 2)
            fig, axes = plt.subplots(nrows, 2, figsize=(16, 4 * nrows), squeeze=False)
            flat = axes.flatten()

            for ax, (_, row) in zip(flat, page.iterrows()):
                try:
                    voltages = parse_float_array(row["Voltages (mV)"])
                except ValueError as e:
                    print(f"cannot plot voltage traces: {e}", file=sys.stderr)
                    plt.close(fig)
                    return
                elapsed = row["MPP Stop Time (s)"] - row["MPP Start Time (s)"]
                time, ver_lines, segments = segment_trace(voltages, elapsed, channels)

                ax.plot(time, voltages, ".", markersize=2, color=CATEGORICAL_COLORS[0], label="ADC samples")
                # everything outside the fitted sweep window is unused
                ax.axvspan(time[0], ver_lines[0], color="#e1e0d9", alpha=0.6, label="outside fitted sweep")
                ax.axvspan(ver_lines[-1], time[-1], color="#e1e0d9", alpha=0.6)
                for v in ver_lines:
                    ax.axvline(x=v, color=CATEGORICAL_COLORS[6], linewidth=1.2)
                for channel, left, right, median in segments:
                    ax.hlines(median, left, right, color=CATEGORICAL_COLORS[7], linewidth=2, zorder=3)
                    ax.annotate(f"ch{channel}", ((left + right) / 2, median), textcoords="offset points",
                                xytext=(0, 6), ha="center", fontsize=7, color="#52514e")

                # a single startup spike (~500 mV) otherwise flattens the whole trace
                lo, hi = np.percentile(voltages, [0.5, 99.5])
                margin = max((hi - lo) * 0.25, 0.05)
                ax.set_ylim(lo - margin, hi + margin)

                ax.set_title(
                    f"{row['Rx']} <- {row['Tx']} | {row[FREQ_COL]} MHz | run {row[RUN_COL]} | "
                    f"rep {row['MPP Repetition']} | phase {row[PHASE_COL]:.2f} deg",
                    fontsize=9,
                )
                ax.set_xlabel("Time [s]")
                ax.set_ylabel("ADC out [mV]")
                ax.grid(color="#e1e0d9", linewidth=0.8)
                ax.set_axisbelow(True)
                ax.legend(fontsize=7, frameon=False, loc="upper left")

            for ax in flat[len(page):]:
                ax.axis("off")

            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

    print(f"saved plot to {output_path} ({len(rows)} traces from runs {[int(r) for r in runs]})")


def plot_grouped_box(data: pd.DataFrame, ylabel: str, title: str, legend_title: str, output_path: str,
                      theoretical: dict = None, theoretical_label: str = "Theoretical") -> None:
    """One subplot per unique series (link / direction), stacked and sharing the frequency axis.

    Overlapping every link in one axes made the boxes unreadable once more than a
    couple of tags were in the CSV; a row each keeps the full box width and lets a
    single link's frequency sweep be read on its own. Saved as PDF (vector) so the
    stack can be zoomed without resampling.
    """
    series_list = sorted(data["series"].unique())
    frequencies = sorted(data["frequency"].unique())

    n_series = len(series_list)
    fig, axes = plt.subplots(
        n_series, 1,
        figsize=(max(8, len(frequencies) * 0.6), max(3.2, 2.6 * n_series)),
        sharex=True,
        squeeze=False,
    )
    axes = axes[:, 0]

    for i, (series, ax) in enumerate(zip(series_list, axes)):
        color = CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)]
        boxes = []
        positions = []
        medians = []
        for j, freq in enumerate(frequencies):
            vals = data.loc[(data["series"] == series) & (data["frequency"] == freq), "value"].values
            if len(vals) == 0:
                continue
            boxes.append(vals)
            positions.append(j)
            medians.append(float(pd.Series(vals).median()))

        if boxes:
            ax.boxplot(
                boxes,
                positions=positions,
                widths=0.6,
                patch_artist=True,
                manage_ticks=False,
                boxprops=dict(facecolor=color, alpha=0.55, color=color, linewidth=1.2),
                medianprops=dict(color=color, linewidth=1.6),
                whiskerprops=dict(color=color, linewidth=1.2),
                capprops=dict(color=color, linewidth=1.2),
                flierprops=dict(
                    marker="o", markersize=4, markerfacecolor=color, markeredgecolor=color, alpha=0.6
                ),
            )
            ax.plot(positions, medians, color=color, linewidth=1.4, linestyle="--", zorder=3)

        if theoretical is not None:
            th_positions = [j for j, freq in enumerate(frequencies) if freq in theoretical]
            th_values = [theoretical[freq] for freq in frequencies if freq in theoretical]
            ax.plot(th_positions, th_values, color="gray", linewidth=1.6, linestyle="--", marker="o",
                    markersize=4, zorder=4, label=theoretical_label)
            ax.legend(frameon=False, fontsize=8, loc="upper right")

        ax.set_title(f"{legend_title}: {series}", fontsize=10, loc="left")
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color="#e1e0d9", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    axes[-1].set_xlim(-0.6, len(frequencies) - 0.4)
    axes[-1].set_xticks(range(len(frequencies)))
    axes[-1].set_xticklabels(frequencies)
    axes[-1].set_xlabel("Frequency (MHz)")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    print(f"saved plot to {output_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Path to the MPP testing CSV file")
    parser.add_argument("--out-dir", default=None, help="Directory for output images (default: alongside the CSV)")
    parser.add_argument("--runs", type=int, default=1,
                        help="Plot voltage traces for the first N run exp numbers (default: 1)")
    parser.add_argument("--freq", type=float, default=None,
                        help="Only include this frequency's rows (MHz) in the voltage-traces PDF (default: all frequencies)")
    parser.add_argument("--distance", type=float, default=None,
                        help="Tag-to-tag distance in meters. If given and the CSV has exactly two unique tags, "
                             "overlay the theoretical phase (2*pi*f*d/c mod pi) on the combined phase plot")
    args = parser.parse_args()

    base = args.csv_path.rsplit(".", 1)[0]
    if args.out_dir:
        import os

        base = os.path.join(args.out_dir, os.path.basename(base))

    combined = load_combined_phases(args.csv_path)
    if combined.empty:
        print("no combined phases could be computed", file=sys.stderr)
    else:
        theoretical = None
        if args.distance is not None:
            tags = pd.read_csv(args.csv_path, usecols=["Rx", "Tx"])
            unique_tags = set(tags["Rx"]) | set(tags["Tx"])
            if len(unique_tags) != 2:
                print(
                    f"--distance given but found {len(unique_tags)} unique tags (need exactly 2); "
                    "skipping theoretical phase overlay",
                    file=sys.stderr,
                )
            else:
                frequencies = sorted(combined["frequency"].unique())
                theoretical = dict(zip(frequencies, theoretical_phase_deg(frequencies, args.distance)))

        plot_grouped_box(
            combined,
            ylabel="Combined phase (deg, mod 180)",
            title="Combined link phase vs. frequency across run exp numbers",
            legend_title="Link",
            output_path=base + "_combined_phase.pdf",
            theoretical=theoretical,
            theoretical_label=f"Theoretical (d={args.distance:g} m)" if theoretical is not None else "Theoretical",
        )

    avg_voltage = load_avg_voltage(args.csv_path)
    if avg_voltage.empty:
        print("no average voltages could be computed", file=sys.stderr)
    else:
        plot_grouped_box(
            avg_voltage,
            ylabel="Average voltage across channels (mV)",
            title="Average voltage (all channels) vs. frequency across run exp numbers",
            legend_title="Direction",
            output_path=base + "_avg_voltage.pdf",
        )

    pdf_suffix = f"_voltage_traces_{args.freq:g}MHz.pdf" if args.freq is not None else "_voltage_traces.pdf"
    plot_voltage_traces(args.csv_path, args.runs, base + pdf_suffix, freq=args.freq)


if __name__ == "__main__":
    main()

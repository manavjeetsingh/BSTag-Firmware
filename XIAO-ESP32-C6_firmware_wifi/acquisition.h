#ifndef ACQUISITION_H
#define ACQUISITION_H

#include <Arduino.h>

/* True while the RF path must not be disturbed. */
bool pathIsBusy(void);

/* --- buffered capture (rdb/rds) --- */

bool captureActive(void);
void captureReset(void);
void captureStart(void);        /* forces CAPTURE_CHANNEL, drops the plotter */
void captureStop(void);
void captureSample(void);       /* call from loop() while captureActive() */
void dumpCapture(Print &out);

/* --- plotter stream (spl/epl) --- */

bool plotterActive(void);
void plotterStart(int session_idx);   /* forces PLOTTER_CHANNEL */
void plotterStop(void);
void plotterSessionClosed(int session_idx);   /* drop stream if it owned it */
void servicePlotter(void);      /* call from loop() while plotterActive() */

/* --- MPP sweep --- */

void runMppSweep(uint16_t passes, Print &out);

#endif /* ACQUISITION_H */

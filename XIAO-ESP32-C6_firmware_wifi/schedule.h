#ifndef SCHEDULE_H
#define SCHEDULE_H

#include <Arduino.h>

/* Slotted schedule: the "multiple" collection mode.
 *
 * A program is an ordered list of equal-length slots, loaded ahead of time
 * and then started by a single trigger that every tag hears at once -- the
 * exciter's ASK preamble (esync, wireless) or sgo (Serial, wired). Each tag
 * transmits in exactly one slot and listens in the others, so one round
 * measures every direction instead of one transmitter at a time.
 *
 * The slot grid is absolute: boundary k sits at k * SCHED_SLOT_US from the
 * trigger, and a command that finishes early is padded out to it. That is
 * what keeps physically separate tags in step without a shared clock -- a
 * running total of however long each command actually took would drift.
 *
 * Results stay as raw ADC codes in the pool until schedReport() streams
 * them, so a round's traces never pass through the 16 KB deferred-reply
 * buffer that qr uses.
 *
 * Loading is: sqc, then one sq_mpp / sq_lis per slot in order, then the
 * optional sqd_/sqn_/squ_ settings. A loaded program takes over the esync
 * fire from the single q_<cmd> slot; see esyncListening(). */

/* Drop the program and any results. */
void schedClear(void);

/* Append one slot. kind is SCHED_KIND_MPP or SCHED_KIND_LISTEN. False when
 * the program is already SCHED_MAX_SLOTS long. */
#define SCHED_KIND_MPP     1
#define SCHED_KIND_LISTEN  2
bool schedAddSlot(uint8_t kind);

/* Settings. Each clamps rather than failing, and returns what it took. */
uint32_t schedSetSlotUs(uint32_t us);
uint32_t schedSetListenSamples(uint32_t n);
void     schedSetRawUnit(bool raw);

/* True once at least one slot is loaded: what makes the esync fire run the
 * program instead of the single queued command. */
bool schedLoaded(void);
uint8_t schedSlotCount(void);

/* True once a program has run and its results are waiting for sqr. */
bool schedHasResult(void);

/* Run the whole program, blocking until the last slot's boundary.
 *
 * Blocking on purpose. Over WiFi this is called with the radio already
 * suspended for the esync window, so nothing else needs the CPU, and a
 * tight loop is what makes the slot boundaries land where they are meant
 * to -- driving it from loop() would put every other poll in the path
 * between a boundary and the sample that follows it. */
void schedRun(void);

/* The loaded program, for sq. */
void schedPrint(Print &out);

/* Every slot's result as one JSON object. Streams from the pool, so it
 * must be given a buffered sink (see BufferedOut). */
void schedReport(Print &out);

#endif /* SCHEDULE_H */

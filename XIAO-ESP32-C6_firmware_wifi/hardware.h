#ifndef HARDWARE_H
#define HARDWARE_H

#include <Arduino.h>

/* Currently selected RF/tag channel, 1-8. */
extern uint8_t current_channel;

/* Power rail, LEDs, RF switch pins and the ADC SPI bus. */
void hardwareInit(void);

void setStatusLed(bool on);
void setUserLed(bool on);

bool switchChannel(uint8_t channel);

uint16_t readAdcRaw(void);
float    rawToMilliVolts(uint16_t raw);
uint16_t milliVoltsToRaw(float mv);

#endif /* HARDWARE_H */

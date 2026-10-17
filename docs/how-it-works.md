# how it works

notes from figuring this out on a vivobook 15x (k3502za, realtek alc294).

## why it sounds bad

the speakers are fine. there's no hidden amp or woofer that linux forgets to
turn on. i checked: forced the unused speaker pin (`0x17`) on and muted the
main one, and got nothing.

on the other OS the audio goes through DTS first. that's an EQ that fixes the
speakers' frequency response, plus a compressor that makes everything louder.
linux just plays the raw signal.

## where the tuning lives

asus has a DTS driver package for these laptops. it's an exe, but 7z opens it.
there's a zip inside it, and that has one folder per laptop, named by the
codec's subsystem id:

```
$ grep Subsystem /proc/asound/card0/codec#0
Subsystem Id: 0x1043175f

ASUS1043175F/
  dts_apo4_oem_config_1043175F.xml
  dts_gpeq_mode1_48k_1043175F.bin
  dts_gpeq_mode1_44k_1043175F.bin
```

there are 256 of these folders, so this probably isn't just my laptop.

the xml is readable. for music mode on the built-in speakers it says: run
this eq file, then a 3-band compressor (500 / 8000 Hz split), then cut
everything under 100 Hz.

## the eq file

the .bin is 4720 bytes, mostly zeros:

```
00000000: 1800 0000 0000 0000 0200 0000 c00c 693f
00000010: 80e6 2d81 c00c 693f 00b5 d07e 0083 2cc1
```

two header ints, then blocks of six ints: `2, b0, b1, b2, a1, a2`. those
are biquad filter coefficients in fixed point (divide by 2^30). a1 and a2 are
stored with the sign flipped. there are 12 of them.

the 44k and 48k files have different numbers but
give the same curve, so the decoding is right:

```
   30 Hz  -21 dB     cut, speaker can't play it
  150 Hz   +4 dB     bass bump
  500 Hz   -6 dB     less boxy
   3 kHz   -6 dB
  12 kHz   +6 dB     more air
```

i run the 12 filters on a single click and save the result as a small wav.
pipewire's convolver plays that back, so the eq matches the original exactly.

## the part that's a guess

the compressor. the xml has the split points, ratios and timings, so those
are copied as is. but the loudness numbers (`Boost = 49`, `Thresh = -100`)
use some scale i couldn't figure out. i set those by ear.

i also added a bass enhancer in front. it adds harmonics so small speakers
sound like they have more bass. [asahi linux](https://github.com/AsahiLinux/asahi-audio)
does the same thing on macbooks. it has to go before the eq, because the eq
throws away most of the low end.

## what runs

```
bass enhancer -> dts eq -> 3-band compressor -> 100 Hz cut -> limiter -> speakers
```

it only sits in front of the built-in speakers. headphones and bluetooth skip
it, and audio switches to them when you connect.

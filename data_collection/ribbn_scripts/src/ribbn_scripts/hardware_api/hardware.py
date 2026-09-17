import serial
import socket
import pandas as pd
import numpy as np
import time
import json

WIFI_PORT = 3333
WIFI_TIMEOUT = 10 #sec

BLADERF_PORT = 3334 #control port of bladerf_exciter_server.py
BLADERF_TIMEOUT = 5 #sec

tag1_mac = b'EC:62:60:4D:34:8C\r\n'
tag2_mac = b'94:3C:C6:6D:53:5C\r\n'
tag3_mac = b'10:97:BD:D4:05:10\r\n'
tag4_mac = b'94:3C:C6:6D:29:2C\r\n'
pos_mac = [tag1_mac, tag2_mac, tag3_mac, tag4_mac]

class Exciter:
    def __init__(self):
        import pyvisa
        rm = pyvisa.ResourceManager()
        self.inst = rm.open_resource('GPIB0::19::INSTR')

    def set_freq(self, freq):
        freq_str = "FREQ:CW " + str(freq) + "MHZ"
        self.inst.write(freq_str)

    def set_pwr(self, pwr):
        pwr_str = "POW:AMPL " + str(pwr) + "DBM;:OUTP:STAT ON"
        self.inst.write(pwr_str)


class BladeRFExciter:
    """A bladeRF on ANOTHER machine, over the control link of
    BladeRFCode/null_sync/bladerf_exciter_server.py.

    Not the usual way to drive one. A bladeRF in this machine's USB port is
    driven in process -- exciters.make_bladerf builds a bladerf_cw.CWExciter
    and calls it directly, with no server to start and no socket. This class
    is for the split setup: tags here, radio (and flowgraph) over there.

    Same set_freq/set_pwr/blank as CWExciter, so nothing above this cares
    which of the two it holds; the only difference is that each call costs a
    round trip and the server has to already be running -- there is nothing
    here that can bring a transmitter up.

    set_pwr takes the run's EXC_POWER, and for this exciter that number is
    TX GAIN IN dB, not a level: a bladeRF has no calibrated output to ask
    for one. It goes to the radio as given. See POWER IS GAIN in
    bladerf_cw.py.
    """

    def __init__(self, host="127.0.0.1", port=BLADERF_PORT,
                 timeout=BLADERF_TIMEOUT):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.sock = None
        self._buf = b''
        self.connect()

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
        except OSError as e:
            raise Exception(
                f"could not reach the bladeRF exciter server at "
                f"{self.host}:{self.port} ({e}). This path is only for a "
                f"bladeRF on another machine, and needs "
                f"bladerf_exciter_server.py running there. If the bladeRF is "
                f"in this machine, clear BLADERF_HOST in configurations.json "
                f"-- the run then drives the radio itself, with no server.")
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._buf = b''

    def _cmd(self, line):
        """Send one command, return the server's reply, raise on 'err ...'.

        Synchronous by design: every caller here is setting up the carrier
        the next measurement runs against, so a write that did not land has
        to be an exception and not a silently wrong run.
        """
        if self.sock is None:
            raise Exception("bladeRF exciter link is closed")
        self.sock.sendall((line + "\n").encode("ascii"))

        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                raise Exception(f"bladeRF exciter did not answer {line!r} "
                                f"within {self.timeout}s")
            if not chunk:
                raise Exception(f"bladeRF exciter closed the link on {line!r}")
            self._buf += chunk

        raw, self._buf = self._buf.split(b"\n", 1)
        reply = raw.decode("ascii", "replace").strip()
        if reply.startswith("err"):
            raise Exception(f"bladeRF exciter rejected {line!r}: {reply}")
        return reply

    def ping(self):
        return self._cmd("ping")

    def status(self):
        return self._cmd("status")

    def set_freq(self, freq):
        """Tune the EMITTED carrier to `freq` MHz (same units as Exciter)."""
        return self._cmd(f"freq {freq}")

    def set_pwr(self, pwr):
        """Set the TX gain in dB -- not a level, see the class docstring.

        A negative request (the -30 the tooling parks the exciter at between
        runs) is below any gain the radio has, so the server reads it as off
        and mutes the carrier in baseband, which is a deeper off than
        minimum gain.
        """
        return self._cmd(f"power {pwr}")

    def blank(self, hold_s):
        """Take the carrier to zero for `hold_s`, then bring it back.

        This is the sync blank the tags time off: they latch the falling
        edge and dead-reckon from it. Returns (t_drop, t_restore) as host
        wall clock, which is bookkeeping only -- the real blank starts once
        the samples already queued in the sink have drained, a few tens of
        ms after this call, and the tags do not care because they time off
        the edge rather than off anything the host knows.
        """
        t_drop = time.time()
        self._cmd(f"blank {hold_s*1e3:g}")
        # The gate's length is exact; this sleep is here so the caller does
        # not go on to the next round while the carrier is still down.
        time.sleep(hold_s)
        return t_drop, time.time()

    def carrier_off(self):
        return self._cmd("off")

    def carrier_on(self):
        return self._cmd("on")

    def close(self):
        if self.sock is None:
            return
        try:
            self._cmd("quit")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None


class Tag:
    def __init__(self, com_str=None, wifi_host=None, wifi_port=WIFI_PORT,
                 wifi_retries=None, wifi_timeout=WIFI_TIMEOUT):
        """
            Either transport, or both. com_str opens the serial port as
            before; wifi_host opens the TCP command channel instead of (or
            as well as) it, which is what a tag reached only over the
            network needs -- there is no serial cable to fall back to, so
            connect() must not be on the construction path at all.

            Both stay optional so the object can also be built bare and
            connected later (connect()/connect_wifi()).
        """
        self.com_str = com_str
        self.ser = None
        self.sock = None
        self._wifi_buf = b''
        self._wifi_host = None
        self._wifi_port = None
        self.resetTime=60 #sec
        if com_str is not None:
            self.connect()
        if wifi_host is not None:
            self.connect_wifi(wifi_host, wifi_port, timeout=wifi_timeout,
                              retries=wifi_retries)

    @classmethod
    def over_wifi(cls, host, port=WIFI_PORT, retries=None, timeout=WIFI_TIMEOUT):
        """
            Convenience constructor for a tag with no serial cable attached.
        """
        return cls(com_str=None, wifi_host=host, wifi_port=port,
                   wifi_retries=retries, wifi_timeout=timeout)

    def connect(self):
        not_connected = 1
        while not_connected:
            try:
                # print("Connect started for",self.com_str)

                self.ser = serial.Serial(port=self.com_str, baudrate=115200, parity=serial.PARITY_NONE,
                                         stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS, timeout=0)
                not_connected = 0
                # print("Connect done for",self.com_str)

            except:
                print('couldnt connect. retrying in 1 sec')
                time.sleep(1)
                continue

    def disconnect(self):
        if self.ser is None:
            return
        try:
            self.ser.close()
        except:
            print('error disconnecting')

    def connect_wifi(self, host, port=WIFI_PORT, timeout=WIFI_TIMEOUT,
                     retries=None):
        """
            Opens a TCP connection to the tag's WiFi command port (TCP_PORT
            in the firmware), the same one `nc <ip> 3333` talks to. This is
            the WiFi counterpart to connect(): the _wifi variants below
            send/receive the same text commands over this raw socket
            instead of over self.ser. No makefile()/buffered-stream layer
            is used here -- just send()/recv(), like nc.

            retries=None keeps the original behaviour of retrying forever,
            which is what a worker that knows its tag is there wants. A
            finite count is for probing an address that may be stale (a
            recorded IP handed to some other host by DHCP): there the
            connect failing is the answer, not something to wait out.
        """
        attempts = 0
        while True:
            try:
                self.sock = socket.create_connection((host, port), timeout=timeout)
                self.sock.settimeout(0.05)
                self._wifi_buf = b''
                self._wifi_host = host      # remembered for reconnect_wifi()
                self._wifi_port = port
                return
            except Exception:
                attempts += 1
                if retries is not None and attempts >= retries:
                    raise
                print('couldnt connect via wifi. retrying in 1 sec')
                time.sleep(1)
                continue
    
    def get_ip_port(self, timeout=5):
        """
            Asks the tag over serial where it is on the network. The reply is
            {"net":"up","ip":...,"port":...,"rssi":...} once associated, and
            {"net":"down"|"suspended"|"disabled"} otherwise.

            Read through _read_json rather than a single readline(): the port
            is opened with timeout=0, so the blob can arrive split across
            reads and a bare readline() would hand back half a line.
        """
        try:
            discard_read=self.ser.readline()
            self.ser.write(bytes("net\r\n", "UTF8"))
            return self._read_json(self.ser.readline, timeout, "get_ip_port")
        except Exception as e:
            # self.disconnect()
            raise e # raising becuase don't want to do silent fail.

    def wifi_on(self, timeout=5):
        """
            Brings the radio back up over serial (the firmware's wifi_on,
            i.e. wifiResume()). Only does anything on a tag whose radio was
            suspended -- an esync window that timed out, or an explicit
            wifi_off. A tag built with an empty WIFI_SSID stays disabled.
        """
        discard_read = self.ser.readline()
        self.ser.write(bytes("wifi_on\r\n", "UTF8"))
        self._read_until_contains(self.ser.readline, "wifi:on", timeout,
                                  "no valid answer timeout")

    def disconnect_wifi(self):
        if self.sock is None:
            return
        try:
            self.sock.close()
        except Exception:
            print('error disconnecting wifi')

    def reconnect_wifi(self, deadline_s=60, poll_s=0.5, host=None, port=None):
        """
            Reopens the TCP channel the firmware dropped when it suspended
            the radio for an esync window. No serial involved: retrying the
            connect IS the readiness check, since the listener only comes
            back once the radio is up.

            That also makes this the signal that the window is over -- it
            returns either because the edge fired or because the firmware
            hit ESYNC_WIFI_TIMEOUT_MS. Ask qr which it was.

            connect_wifi() retries forever, so the deadline lives here.
        """
        host = host if host is not None else self._wifi_host
        port = port if port is not None else self._wifi_port
        if host is None:
            raise RuntimeError("no previous wifi connection to reconnect to")

        if self.sock is not None:
            self.disconnect_wifi()
            self.sock = None

        end = time.time() + deadline_s
        last = None
        while time.time() < end:
            try:
                sock = socket.create_connection((host, port), timeout=2.0)
            except OSError as e:
                last = e
                time.sleep(poll_s)
                continue

            sock.settimeout(0.05)
            self.sock = sock
            self._wifi_buf = b''
            self._wifi_host = host
            self._wifi_port = port
            return

        raise TimeoutError(
            f"{host}:{port} did not come back in {deadline_s}s, last: {last}")

    def fetch_queued_wifi(self, timeout=WIFI_TIMEOUT, ack=None):
        """
            Pulls the reply the firmware buffered while the radio was down.
            Returns the command's own JSON, or {"info":"qr","pending":0} if
            no queued command ran -- which is how a timed-out window (no
            exciter edge) is told apart from a good one.

            `ack` is for staged commands that do NOT answer in JSON: rdb
            replies with the plain line "rdb", so qr hands that line back
            and there is no blob to parse. Pass the token to expect
            (queue_capture_wifi -> "rdb") and it comes back wrapped as
            {"info":"qr","pending":1,"cmd":ack}, so callers can keep
            reading ["pending"] either way. Left as None only JSON is
            accepted, which is what a staged adc_/mpp_/mac gives.
        """
        discard_read = self._wifi_readline()
        self._wifi_write(bytes("qr\r\n", "UTF8"))
        if ack is None:
            return self._read_json(self._wifi_readline, timeout,
                                   "fetch_queued_wifi")
        return self._read_queued_ack(ack, timeout, "fetch_queued_wifi")

    def _read_queued_ack(self, ack, timeout, what):
        """
            qr for a staged command whose reply is plain text.

            Two answers are possible and only one of them is JSON: the
            firmware's own {"info":"qr","pending":0} (or the overflow blob)
            when nothing fired, and the staged command's bare ack when it
            did. JSON is tested first, since the overflow reply carries the
            command name and would otherwise match `ack` itself.
        """
        buffer = ""
        read_start_time = time.time()
        while True:
            if time.time() - read_start_time > timeout:
                print(f"{what} timed out after {timeout}s waiting for {ack!r} "
                      f"or a JSON reply; received {len(buffer)} chars so far: "
                      f"{buffer[:300]!r}")
                raise Exception("Stuck in " + what)

            line = self._wifi_readline()
            if len(line) == 0:
                time.sleep(0.001)
                continue

            buffer += line.decode(errors='ignore')

            start = buffer.find('{')
            end = buffer.rfind('}')
            if start != -1 and end > start:
                try:
                    return json.loads(buffer[start:end + 1])
                except Exception:
                    pass

            if ack in buffer:
                return {"info": "qr", "pending": 1, "cmd": ack}

    def esync_report(self, timeout=WIFI_TIMEOUT):
        """
            Serial counterpart to esync_report_wifi(). The detector no longer
            prints the edge as it happens -- that write sat between the edge
            and the dispatch -- so this is how the timing is read back.
        """
        discard_read = self.ser.readline()
        self.ser.write(bytes("esyncr\r\n", "UTF8"))
        return self._read_json(self.ser.readline, timeout, "esync_report")

    def esync_report_wifi(self, timeout=WIFI_TIMEOUT):
        """
            The edge that fired the queued command: t_us (the falling edge
            it timed off), fire_us (when the command actually ran), low_us
            (the blank as measured), and the min/baseline levels it was
            judged against.

            The tag commits at the falling edge and fires delay_us later
            without waiting to see the blank end, so low_us is an
            after-the-fact check rather than something the tag acted on:
            it should read back as the exciter's DROP_MS. "returned":0
            means the carrier had not come back by the time it fired.
            Useful for checking threshold margin and for comparing low_us
            across tags.
        """
        discard_read = self._wifi_readline()
        self._wifi_write(bytes("esyncr\r\n", "UTF8"))
        return self._read_json(self._wifi_readline, timeout, "esync_report_wifi")

    def _wifi_write(self, data):
        self.sock.sendall(data)

    def _wifi_readline(self):
        """
            Raw-socket equivalent of self.ser.readline() with timeout=0:
            returns whatever line is available in the internal buffer, or
            b'' (without blocking) if a full line hasn't arrived yet.
        """
        nl = self._wifi_buf.find(b'\n')
        if nl == -1:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                chunk = b''
            except OSError:
                chunk = b''
            if chunk:
                self._wifi_buf += chunk
            nl = self._wifi_buf.find(b'\n')

        if nl == -1:
            return b''

        line = self._wifi_buf[:nl + 1]
        self._wifi_buf = self._wifi_buf[nl + 1:]
        return line

    def listen_esync(self):
        """
            makes tag listen for esync
        """
        discard_read=self.ser.readline()
        self.ser.write(bytes("esync"+"\r\n", "UTF8"))
        c_str="esync:listening\r\n"
        self._read_until_contains(self.ser.readline, c_str, 5, 'no valid answer timeout')

    def listen_esync_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to listen_esync().
        """
        discard_read=self._wifi_readline()
        self._wifi_write(bytes("esync"+"\r\n", "UTF8"))
        c_str="esync:listening\r\n"
        self._read_until_contains(self._wifi_readline, c_str, timeout, 'no valid answer timeout')

    def stop_esync(self):
        """
            Disarms the edge detector (`esyncs`).

            The counterpart to listen_esync(). Needed because arming is
            sticky in a way that outlives the host: a run killed between
            `esync` and the edge leaves the tag listening, and the firmware
            only resumes its radio after ESYNC_WIFI_TIMEOUT_MS -- it does
            not stop listening. The next run then finds a tag already
            armed, reporting "listening":1 from somebody else's window.
        """
        discard_read=self.ser.readline()
        self.ser.write(bytes("esyncs\r\n", "UTF8"))
        self._read_until_contains(self.ser.readline, "esync:stopped", 5,
                                  'no valid answer timeout')

    def stop_esync_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to stop_esync().
        """
        discard_read=self._wifi_readline()
        self._wifi_write(bytes("esyncs\r\n", "UTF8"))
        self._read_until_contains(self._wifi_readline, "esync:stopped",
                                  timeout, 'no valid answer timeout')

    def clear_queued_command(self):
        """
            Drops whatever command is staged to fire on the next edge
            (`qc`).

            Note this is the staged COMMAND, not the reply it leaves
            behind -- that is qr's, and esync clears it on the next arm.
            A q_ that never fired stays staged indefinitely, so without
            this a run that died after staging leaves the next `esync` to
            fire a command nobody asked for.
        """
        discard_read=self.ser.readline()
        self.ser.write(bytes("qc\r\n", "UTF8"))
        self._read_until_contains(self.ser.readline, "q:cleared", 5,
                                  'no valid answer timeout')

    def clear_queued_command_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to clear_queued_command().
        """
        discard_read=self._wifi_readline()
        self._wifi_write(bytes("qc\r\n", "UTF8"))
        self._read_until_contains(self._wifi_readline, "q:cleared", timeout,
                                  'no valid answer timeout')

    def queue_any(self, command_str):
        discard_read=self.ser.readline()
        
        q_command="q_"+command_str
        c_str=f"q:queued, {command_str}\n"
        self.ser.write(bytes(q_command+"\r\n", "UTF8"))

        self._read_until_contains(self.ser.readline, c_str, 5, 'no valid answer timeout')


    def queue_any_wifi(self, command_str, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to queue_any().
        """
        discard_read=self._wifi_readline()

        q_command="q_"+command_str
        c_str=f"q:queued, {command_str}\n"
        self._wifi_write(bytes(q_command+"\r\n", "UTF8"))

        self._read_until_contains(self._wifi_readline, c_str, timeout, 'no valid answer timeout')

    def queue_adc_read(self, num_samples):
        command=f"adc_{num_samples}"
        self.queue_any(command)

    def queue_adc_read_wifi(self, num_samples, timeout=WIFI_TIMEOUT):
        command=f"adc_{num_samples}"
        self.queue_any_wifi(command, timeout)

    def queue_mpp(self, num):
        command=f"mpp_{num}"
        self.queue_any(command)

    def queue_mpp_wifi(self, num, timeout=WIFI_TIMEOUT):
        command=f"mpp_{num}"
        self.queue_any_wifi(command, timeout)

    def queue_capture(self):
        """
            Stages a buffered capture (rdb) to start on the next esync edge.

            Unlike queue_adc_read(), the trace does NOT come back through
            qr: rdb's own reply is just an ack, and the samples stay in the
            tag's capture buffer until rds asks for them. That is the point
            of using it here -- the capture runs at the full sample-loop
            rate and is not bounded by QUEUED_REPLY_BUF_LEN, and the
            firmware holds the radio down until captureActive() clears (see
            loop() in the .ino) so the window covers the whole capture.

            So the sequence is: queue_capture() -> esync -> reconnect ->
            qr (the ack) -> stop_reading() (the trace).
        """
        self.queue_any("rdb")

    def queue_capture_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to queue_capture().
        """
        self.queue_any_wifi("rdb", timeout)

    def read_queued(self, timeout=None):
        """
            Blocks until the queued command fires on the next esync edge and
            returns its reply, parsed as JSON. Works for any command whose
            reply is a JSON blob (adc, adcraw, rds, mpp, mac, net); commands
            that answer in plain text (ch_) will time out here.

            Defaults to self.resetTime rather than the few seconds the other
            calls use, since the wait is for the exciter edge, not the tag.
        """
        if timeout is None:
            timeout = self.resetTime

        return self._read_json(self.ser.readline, timeout, "read_queued")

    def read_queued_wifi(self, timeout=None):
        """
            WiFi counterpart to read_queued(). Also defaults to
            self.resetTime instead of WIFI_TIMEOUT: what is being waited on
            is the exciter edge, which the link speed has no bearing on.
        """
        if timeout is None:
            timeout = self.resetTime

        return self._read_json(self._wifi_readline, timeout, "read_queued_wifi")
    
    def reflect(self, ch):
        discard_read=self.ser.readline()
        c_str = 'ch: ' + str(ch) + ', ok\r\n'

        while True:
            try:
                # self.connect()
                discard_read=self.ser.readline()
                # print("DR",discard_read)
                self.ser.write(bytes("ch_"+str(ch)+"\r\n", "UTF8"))
                self._read_until_contains(self.ser.readline, c_str, 5, 'no valid answer timeout')
                return
            except Exception as e:
                # self.disconnect()
                raise e

    def reflect_wifi(self, ch, timeout=WIFI_TIMEOUT):
        c_str = 'ch: ' + str(ch) + ', ok\r\n'

        while True:
            try:
                discard_read = self._wifi_readline()
                self._wifi_write(bytes("ch_"+str(ch)+"\r\n", "UTF8"))
                self._read_until_contains(self._wifi_readline, c_str, timeout, 'no valid answer timeout')
                return
            except Exception as e:
                raise e

    def begin_reading(self):
        """
            The tag starts reading ADC out and stores it in microcontroller
            memory. 
        """
        self.ser.write(b"rdb\0\n")
        command_start_time=time.time()
        prev=''
        while True:
            if time.time()-command_start_time>self.resetTime:
                print(f"begin_reading timed out after {self.resetTime}s; last received ({len(prev)} chars): {prev[:300]!r}")
                raise Exception("Stuck in begin_reading")

            raw = self.ser.readline()
            if len(raw) == 0:
                time.sleep(0.001)
                continue

            line = prev+raw.decode()
            if "rdb" in line:
                prev=''
                break
            elif line[-1] != "\n":
                prev=line
            else:
                prev=''

    def begin_reading_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to begin_reading().
        """
        self._wifi_write(b"rdb\0\n")
        self._read_until_contains(self._wifi_readline, "rdb", timeout, "Stuck in begin_reading_wifi")

    # def stop_reading(self):
    #     """
    #         Stops the continuous reading and storing of ADC out.
    #         Ideal tag output would be in the following format.
    #         "num_readings,\r\n"
    #         "reading 1,\r\n"
    #         "reading 2,\r\n"
    #         ..
    #         "reading n,\r\n"
    #
    #        Tag output might not be ideal always, it can have some artifacts.
    #        This function processes that and returns the values as an array.
    #     """
    #     self.ser.write(b"rds\0\n")
    #     voltages=""
    #
    #     read_start_time=time.time()
    #     while True:
    #         if time.time()-read_start_time>self.resetTime:
    #             break
    #         line = self.ser.readline()
    #         if len(line) > 0:
    #             # ss = str(line)
    #             ss = str(line)
    #             # print(ss)
    #
    #             if "end" in ss:
    #                 break
    #             try:
    #                 to_add=line.decode()
    #                 to_add=to_add.replace("\n",'')
    #                 to_add=to_add.replace("\r",'')
    #                 voltages+=to_add
    #             except Exception as e:
    #                 print(e)
    #     return self.clean_voltage_data(voltages)

    def stop_reading(self):
        """
            Stops the continuous reading and requests the buffered ADC data.
            Tag output is a JSON blob of the form:
            {"info":"buf","ch":2,"unit":"mV","count":10000,"full":1,"data":"1.678,1.526,...,3.052"}

            Since the serial port is opened with timeout=0, readline() returns
            whatever bytes happen to be buffered at each poll rather than whole
            lines, so the JSON arrives split across many reads. Bytes are
            accumulated here until a balanced {...} blob can be parsed.
        """
        self.ser.write(b"rds\0\n")

        buffer = ""
        read_start_time = time.time()
        while True:
            if time.time() - read_start_time > self.resetTime:
                print(f"stop_reading timed out after {self.resetTime}s; received {len(buffer)} chars so far: {buffer[:300]!r}")
                raise Exception("Stuck in stop_reading")

            line = self.ser.readline()
            if len(line) == 0:
                time.sleep(0.001)
                continue

            try:
                buffer += line.decode(errors='ignore')
            except Exception as e:
                print(e)
                continue

            start = buffer.find('{')
            end = buffer.rfind('}')
            if start == -1 or end == -1 or end < start:
                continue

            try:
                payload = json.loads(buffer[start:end + 1])
            except Exception:
                continue

            return self.clean_buf_data(payload)

    def stop_reading_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to stop_reading(). Same JSON-blob buffering
            approach applies: a 10000-sample dump spans many TCP segments,
            so bytes are accumulated until a balanced {...} can be parsed.
        """
        self._wifi_write(b"rds\0\n")

        buffer = ""
        read_start_time = time.time()
        while True:
            if time.time() - read_start_time > timeout:
                print(f"stop_reading_wifi timed out after {timeout}s; received {len(buffer)} chars so far: {buffer[:300]!r}")
                raise Exception("Stuck in stop_reading_wifi")

            line = self._wifi_readline()
            if len(line) == 0:
                continue

            try:
                buffer += line.decode(errors='ignore')
            except Exception as e:
                print(e)
                continue

            start = buffer.find('{')
            end = buffer.rfind('}')
            if start == -1 or end == -1 or end < start:
                continue

            try:
                payload = json.loads(buffer[start:end + 1])
            except Exception:
                continue

            return self.clean_buf_data(payload)

    def _read_until_contains(self, readline, target, timeout, timeout_msg):
        """
            Reads lines via `readline` until `target` shows up, keeping a
            `prev` suffix across reads so a target string split across two
            reads (e.g. b'{"info":"mp' + b'p","ch":8,...}') is still caught
            instead of missed by a single readline() check.
        """
        start_time = time.time()
        prev = ''
        while True:
            if time.time() - start_time > timeout:
                print(f"{timeout_msg} after {timeout}s waiting for {target!r}; last received ({len(prev)} chars): {prev[:300]!r}")
                raise Exception(timeout_msg)

            raw = readline()
            line = raw.decode(errors='ignore') if isinstance(raw, bytes) else raw
            if len(line) == 0:
                time.sleep(0.001)
                continue

            combined = prev + line
            if target in combined:
                return combined

            if combined.endswith('\n'):
                prev = ''
            else:
                prev = combined

    def _read_json(self, readline, timeout, what):
        """
            Accumulates bytes from `readline` until a balanced {...} blob can
            be parsed, and returns it. Both transports poll without blocking
            (serial timeout=0, socket timeout=0.05), so any reply bigger than
            one read arrives split across several of them.
        """
        buffer = ""
        read_start_time = time.time()
        while True:
            if time.time() - read_start_time > timeout:
                print(f"{what} timed out after {timeout}s; received {len(buffer)} chars so far: {buffer[:300]!r}")
                raise Exception("Stuck in " + what)

            line = readline()
            if len(line) == 0:
                time.sleep(0.001)
                continue

            buffer += line.decode(errors='ignore')

            start = buffer.find('{')
            end = buffer.rfind('}')
            if start == -1 or end == -1 or end < start:
                continue

            try:
                return json.loads(buffer[start:end + 1])
            except Exception:
                continue

    def clean_adc_data(self, payload):
        """
            Parses the "data" field of an adc JSON payload into an array of
            floats: millivolts for "unit":"mV", ADC codes for "unit":"raw".
        """
        clean_data = []
        for d in payload["data"].split(','):
            try:
                clean_data.append(float(d))
            except Exception as e:
                print(e)

        return np.array(clean_data)

    def clean_buf_data(self, payload):
        """
            Parses the "data" field of a buffer JSON payload into an array of floats.
        """
        data = payload["data"].split(',')
        clean_data = []
        for d in data:
            try:
                clean_data.append(float(d))
            except Exception as e:
                print(e)
        assert (len(clean_data) <= payload["count"])

        return np.array(clean_data)
    
    def clean_voltage_data(self, voltages):
        """
            Cleans the tag output data and converts it to an array.
        """
        data=voltages.split(',')
        max_num=int(data[0])
        data=data[1:]
        clean_data=[]
        for d in data:
            try:
                clean_data.append(float(d))
            except Exception as e:
                print(e)
        assert(len(clean_data)<=max_num)
        
        return np.array(clean_data)

    def perform_mpp(self, passes=1):
        """
            Asking the tag to do the MPP. The tag dwells MPP_DWELL_US (3ms) on
            each entry of MPP_CHANNELS, which is {1,1,1,1,3,4,6,7,8} -- so ch1 is
            held for 12ms and the rest for 3ms each, 27ms per pass. The leading
            ch1 repeats are unmeasured padding that absorbs the rectifier's
            settling ramp; getChannelVoltage() anchors its windows to the end of
            the capture and so reads the last six dwells.
            This is a blocking call.
            Returns MPP start and end times.
        """
        mpp_start_time=time.time()
        self.ser.write(f"mpp_{passes}\0\n".encode())
        self._read_until_contains(self.ser.readline, "mpp", self.resetTime, "Stuch in perfrom_mpp")
        mpp_end_time=time.time()

        return mpp_start_time, mpp_end_time

    def perform_mpp_wifi(self, passes=1, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to perform_mpp().
        """
        mpp_start_time = time.time()
        self._wifi_write(f"mpp_{passes}\0\n".encode())
        self._read_until_contains(self._wifi_readline, "mpp", timeout, "Stuch in perfrom_mpp_wifi")
        mpp_end_time = time.time()

        return mpp_start_time, mpp_end_time

    def startPlotting(self):
        self.ser.write(b"spl\0\n")

    def endPlotting(self):
        self.ser.write(b"epl\0\n")

    def startPlotting_wifi(self):
        self._wifi_write(b"spl\0\n")

    def endPlotting_wifi(self):
        self._wifi_write(b"epl\0\n")

    def get_adc_val(self, count=30, raw=False, timeout=5):
        """
            Reads `count` ADC samples in one burst off the current channel.
            The tag answers with a single JSON blob:
            {"info":"adc","ch":2,"unit":"mV","data":"1.678,1.526,...,3.052"}

            The firmware clamps `count` to MAX_ADC_SAMPLES (1000), so asking
            for more just returns 1000 samples. raw=True issues adcraw_<n>
            instead and returns unscaled ADC codes ("unit":"raw") rather
            than millivolts.
        """
        cmd = "adcraw" if raw else "adc"

        discard_read = self.ser.readline()
        # print("DR",discard_read)
        self.ser.write(f"{cmd}_{count}\0\n".encode())
        payload = self._read_json(self.ser.readline, timeout, "get_adc_val")

        return self.clean_adc_data(payload)

    def get_adc_val_wifi(self, count=30, raw=False, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to get_adc_val().
        """
        cmd = "adcraw" if raw else "adc"

        discard_read = self._wifi_readline()
        self._wifi_write(f"{cmd}_{count}\0\n".encode())
        payload = self._read_json(self._wifi_readline, timeout, "get_adc_val_wifi")

        return self.clean_adc_data(payload)

    def get_mac(self, timeout=5):
        """
            Asks the tag for its MAC. The reply is a JSON blob,
            {"mac":"EC:62:60:4D:34:8C"}, and the address is returned as a
            plain string.
        """
        discard_read=self.ser.readline()
        # print("DR",discard_read)
        self.ser.write(b'mac\0\n')
        payload = self._read_json(self.ser.readline, timeout, "get_mac")

        return payload["mac"]

    def get_mac_wifi(self, timeout=WIFI_TIMEOUT):
        """
            WiFi counterpart to get_mac().
        """
        discard_read = self._wifi_readline()
        self._wifi_write(b'mac\0\n')
        payload = self._read_json(self._wifi_readline, timeout, "get_mac_wifi")

        return payload["mac"]


class VNA:
    def __init__(self,pwr):
        import pyvisa
        rm = pyvisa.ResourceManager()
        self.inst = rm.open_resource('GPIB0::17::INSTR')
        self.inst.write(':SENS1:FREQ:STAR 700E6')
        self.inst.write(':SENS1:FREQ:STOP 3000E6')
        self.inst.write(f':SOUR1:POW {pwr}')
        self.inst.write(':SENS1:SWE:DEL 0.001')
        self.inst.write(':SENS1:SWE:POIN 1000')
        self.inst.write(':CALC1:PAR1:DEF S11')
        # self.inst.write(':MMEM:STOR:FDAT "D:/automated_folder/Trace01.csv"')

    def wtf(self, str):
        x = ':MMEM:STOR:FDAT "D:' + str + '.csv"'
        self.inst.write(x)

    def set_pwr(self, pwr):
        self.inst.write(':SOUR1:POW ' + str(pwr) + '')

    def transfer_file(self, src, dst):
        self.inst.write(':MMEM:TRAN? "D:' + src + '.csv"')
        f = self.inst.read()
        from io import StringIO
        io = StringIO(f)
        pd.DataFrame(pd.read_csv(io, skiprows=2)).to_csv(dst)

#
# vna = VNA()
# vna.set_pwr(-6)
# vna.wtf('/automated_folder/file_name')
# f = vna.transfer_file('/automated_folder/file_name', 'a.csv')

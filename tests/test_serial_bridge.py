"""USB compatibility and ingestion tests use an isolated database, never COM7."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serial_bridge import ReceiverParser, LineDecoder, SerialBridge

LEGACY = '''============================================
RAW: DATA,UG-01,390,48.97,-10.18,0,0.0
Host ID       : HOST-01
Zone ID       : ZONE-A
Node ID       : UG-01
Sequence      : 390
Roll          : 48.97 deg
Pitch         : -10.18 deg
Vibration     : NORMAL
Soil ADC      : 0
RSSI          : -30 dBm
SNR           : 9.75 dB
[ACK] Sending: ACK,UG-01,390
[ACK] Sent successfully.
[OK] Packet processed.
'''


def legacy_packet():
    parser = ReceiverParser()
    result = None
    for line in LEGACY.splitlines():
        result = parser.feed(line, now=10)
    return result


class ParserTests(unittest.TestCase):
    def test_original_receiver_output(self):
        packet, ack = legacy_packet()
        self.assertFalse(ack)
        self.assertEqual(packet['sequence'],390)
        self.assertEqual(packet['rssi'],-30)
        self.assertEqual(packet['snr'],9.75)
        self.assertEqual(packet['roll'],48.97)
        self.assertEqual(packet['pitch'],-10.18)
        self.assertEqual(packet['soil'],0)
        self.assertNotIn('battery',packet)

    def test_incomplete_or_stale_logs_do_not_emit(self):
        for skip in ('RSSI', 'SNR', 'Host ID', 'Zone ID', '[OK]'):
            parser=ReceiverParser()
            results=[parser.feed(line, now=10) for line in LEGACY.splitlines() if not line.startswith(skip)]
            self.assertFalse(any(results))
        parser=ReceiverParser()
        for line in LEGACY.splitlines()[:-1]: parser.feed(line, now=10)
        self.assertIsNone(parser.feed('[OK] Packet processed.', now=16))

    def test_new_packet_cannot_inherit_previous_rssi(self):
        parser=ReceiverParser()
        for line in LEGACY.splitlines()[:-1]: parser.feed(line,now=10)
        parser.feed('RAW: DATA,UG-01,391,0,0,0,0',now=11)
        self.assertIsNone(parser.feed('[OK] Packet processed.',now=11))

    def test_malformed_data_rejected(self):
        for raw in ('DATA,UG-01,abc,0,0,0,0','DATA,UG-01,1,nan,0,0,0','DATA,UG-01,1,0,0,0',
                    'DATA,UG-01,1,0,0,0,0,extra','DATA,OTHER,1,0,0,0,0'):
            parser=ReceiverParser()
            for line in LEGACY.splitlines():
                result=parser.feed('RAW: '+raw if line.startswith('RAW:') else line,now=10)
                self.assertIsNone(result)

    def test_json_and_noise(self):
        packet,_=legacy_packet()
        self.assertEqual(ReceiverParser().feed('TELEMETRY '+json.dumps(packet)),(packet,True))
        for line in ('======','TELEMETRY {bad','TELEMETRY []','ACK,UG-01,1'):
            self.assertIsNone(ReceiverParser().feed(line))

    def test_line_decoder_split_and_oversized(self):
        decoder=LineDecoder()
        self.assertEqual(decoder.feed(b'RAW: DATA,'),[])
        self.assertEqual(decoder.feed(b'UG-01\r\nnoise\n'),['RAW: DATA,UG-01\r','noise'])
        self.assertEqual(decoder.feed(b'x'*9000),[])
        self.assertLessEqual(len(decoder.buffer),2048)
        self.assertEqual(decoder.feed(b'ignored\nvalid\n'),['valid'])


class BridgeTests(unittest.TestCase):
    def test_duplicate_inflight_bounded(self):
        bridge=SerialBridge(Mock())
        packet,_=legacy_packet()
        for _ in range(500): bridge.enqueue((packet,True))
        self.assertEqual(bridge.pending.qsize(),1)
        self.assertEqual(bridge.dropped,0)

    def test_ack_only_after_successful_ingestion(self):
        packet,_=legacy_packet()
        callback=Mock(return_value={'success':True})
        bridge=SerialBridge(callback)
        bridge.port=Mock(is_open=True)
        item=(packet,True,10,('UG-01',390))
        with patch('serial_bridge.time.monotonic',return_value=13):
            bridge.deliver(item)
        self.assertEqual(callback.call_args.args[0]['queue_age_ms'],3000)
        bridge.port.write.assert_called_once_with(b'STORED,UG-01,390\n')
        bridge.port.write.reset_mock()
        callback.side_effect=ValueError('bad telemetry')
        with self.assertRaises(ValueError): bridge.deliver(item)
        bridge.port.write.assert_not_called()

    def test_legacy_does_not_write_to_receiver(self):
        bridge=SerialBridge(Mock(return_value={'success':True}))
        bridge.port=Mock(is_open=True)
        packet,_=legacy_packet()
        bridge.deliver((packet,False,0,('UG-01',390)))
        bridge.port.write.assert_not_called()

    def test_reconnect_after_port_absent(self):
        import serial
        bridge=SerialBridge(Mock())
        port=Mock(is_open=True, in_waiting=len(LEGACY))
        def read(_):
            bridge.stop_event.set()
            return LEGACY.encode()
        port.read.side_effect=read
        with patch('serial.Serial', side_effect=[serial.SerialException('port absent'),port]), patch.object(bridge.stop_event,'wait'):
            bridge.read_loop()
        self.assertEqual(bridge.pending.qsize(),1)
        port.reset_input_buffer.assert_called_once()
        port.close.assert_called_once()

    def test_database_failure_retains_packet_until_success(self):
        import sqlite3
        packet,_=legacy_packet()
        callback=Mock()
        bridge=SerialBridge(callback)
        bridge.port=Mock(is_open=True)
        def store(payload):
            if callback.call_count==1:
                bridge.port.write.assert_not_called()
                raise sqlite3.OperationalError('locked')
            bridge.stop_event.set()
            return {'success':True}
        callback.side_effect=store
        bridge.enqueue((packet,True))
        with patch.object(bridge.stop_event,'wait'):
            bridge.store_loop()
        self.assertEqual(callback.call_count,2)
        bridge.port.write.assert_called_once_with(b'STORED,UG-01,390\n')
        self.assertFalse(bridge.inflight)

    def test_hardware_log_to_existing_flask_pipeline(self):
        import database
        from app import accept_telemetry, app
        with tempfile.TemporaryDirectory() as folder, patch.object(database,'DB_NAME',str(Path(folder)/'test.db')):
            database.init_db()
            database.set_mode('REAL')
            bridge=SerialBridge(accept_telemetry)
            packet,ack=legacy_packet()
            with patch('serial_bridge.time.monotonic',return_value=10):
                item=(packet,ack,10,('UG-01',390))
                self.assertTrue(bridge.deliver(item)['success'])
                self.assertTrue(bridge.deliver(item)['duplicate'])
            client=app.test_client()
            rd=client.get('/api/readings/latest').json[0]
            self.assertEqual(rd['sequence'],390)
            self.assertEqual(rd['data_source'],'REAL')
            self.assertTrue(rd['online'])
            self.assertEqual(len(database.get_history('UG-01')),1)
            self.assertEqual(client.get('/api/twin/state').json['nodes'][0]['tilt_x'],48.97)

if __name__=='__main__': unittest.main()

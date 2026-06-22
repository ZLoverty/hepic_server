#!/usr/bin/env python3
"""
Local unit tests for reconnect and null-payload fixes.
Runs without hardware using mocks.

Usage:
    python tests/test_reconnect.py
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hepic_server.gateway import ModbusGateway, TCPGateway
from pymodbus.exceptions import ModbusException


# ---------------------------------------------------------------------------
# TCPGateway._close()
# ---------------------------------------------------------------------------
class TestTCPGatewayClose(unittest.IsolatedAsyncioTestCase):
    def _gateway_with_mock_connection(self):
        gw = TCPGateway("127.0.0.1", 9999)
        gw.writer = MagicMock()
        gw.reader = MagicMock()
        return gw

    def test_close_calls_writer_close_and_clears_both(self):
        gw = self._gateway_with_mock_connection()
        gw._close()
        gw.writer  # already None now
        # capture before clear
        # re-check via side-effect: just verify state
        gw2 = self._gateway_with_mock_connection()
        mock_writer = gw2.writer
        gw2._close()
        mock_writer.close.assert_called_once()
        self.assertIsNone(gw2.writer)
        self.assertIsNone(gw2.reader)

    def test_close_is_safe_when_already_none(self):
        gw = TCPGateway("127.0.0.1", 9999)
        gw._close()  # should not raise


# ---------------------------------------------------------------------------
# TCPGateway._ensure_connected()
# ---------------------------------------------------------------------------
class TestTCPGatewayEnsureConnected(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_connection_skips_reconnect(self):
        gw = TCPGateway("127.0.0.1", 9999)
        gw.reader = MagicMock(**{"at_eof.return_value": False})
        gw.writer = MagicMock(**{"is_closing.return_value": False})

        with patch("asyncio.open_connection", new=AsyncMock()) as mock_conn:
            result = await gw._ensure_connected()

        mock_conn.assert_not_called()
        self.assertTrue(result)

    async def test_reconnects_when_reader_at_eof(self):
        """at_eof() == True means peer closed the connection — must reconnect."""
        gw = TCPGateway("127.0.0.1", 9999)
        old_writer = MagicMock(**{"is_closing.return_value": False})
        old_reader = MagicMock(**{"at_eof.return_value": True})
        gw.writer = old_writer
        gw.reader = old_reader

        new_reader, new_writer = MagicMock(), MagicMock()
        with patch("asyncio.open_connection", new=AsyncMock(return_value=(new_reader, new_writer))):
            result = await gw._ensure_connected()

        old_writer.close.assert_called_once()  # old transport closed
        self.assertIs(gw.reader, new_reader)
        self.assertIs(gw.writer, new_writer)
        self.assertTrue(result)

    async def test_reconnects_when_writer_is_closing(self):
        gw = TCPGateway("127.0.0.1", 9999)
        old_writer = MagicMock(**{"is_closing.return_value": True})
        old_reader = MagicMock(**{"at_eof.return_value": False})
        gw.writer = old_writer
        gw.reader = old_reader

        new_reader, new_writer = MagicMock(), MagicMock()
        with patch("asyncio.open_connection", new=AsyncMock(return_value=(new_reader, new_writer))):
            result = await gw._ensure_connected()

        old_writer.close.assert_called_once()
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# TCPGateway.exchange() — failure path calls _close(), not just writer=None
# ---------------------------------------------------------------------------
class TestTCPGatewayExchangeCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_calls_close_on_drain_error(self):
        gw = TCPGateway("127.0.0.1", 9999)
        gw.reader = MagicMock(**{"at_eof.return_value": False})
        gw.writer = MagicMock(
            **{
                "is_closing.return_value": False,
                "write": MagicMock(),
                "drain": AsyncMock(side_effect=ConnectionResetError("reset")),
            }
        )
        old_writer = gw.writer

        result = await gw.exchange(b"SI\r\n")

        self.assertIsNone(result)
        old_writer.close.assert_called_once()
        self.assertIsNone(gw.writer)
        self.assertIsNone(gw.reader)

    async def test_exchange_calls_close_on_read_timeout(self):
        gw = TCPGateway("127.0.0.1", 9999, timeout=0.01)
        gw.reader = MagicMock(
            **{
                "at_eof.return_value": False,
                "read": AsyncMock(side_effect=asyncio.TimeoutError()),
            }
        )
        gw.writer = MagicMock(
            **{
                "is_closing.return_value": False,
                "write": MagicMock(),
                "drain": AsyncMock(),
            }
        )
        old_writer = gw.writer

        result = await gw.exchange(b"SI\r\n")

        self.assertIsNone(result)
        old_writer.close.assert_called_once()
        self.assertIsNone(gw.writer)
        self.assertIsNone(gw.reader)


# ---------------------------------------------------------------------------
# ModbusGateway — consecutive failure counting and reconnect
# ---------------------------------------------------------------------------
class TestModbusGatewayReconnect(unittest.IsolatedAsyncioTestCase):
    def _make_gateway(self, connected=True, execute_side_effect=None):
        """Build a ModbusGateway with a mocked pymodbus client."""
        gw = ModbusGateway.__new__(ModbusGateway)
        gw._lock = asyncio.Lock()
        gw._consecutive_failures = 0
        mock_client = MagicMock()
        mock_client.connected = connected
        mock_client.execute = AsyncMock(
            side_effect=execute_side_effect or ModbusException("no response")
        )
        gw.client = mock_client
        return gw

    async def test_failures_below_threshold_do_not_close(self):
        gw = self._make_gateway()
        threshold = ModbusGateway._CONSECUTIVE_FAIL_THRESHOLD

        for i in range(threshold - 1):
            result = await gw.exchange(MagicMock())
            self.assertIsNone(result)

        gw.client.close.assert_not_called()
        self.assertEqual(gw._consecutive_failures, threshold - 1)

    async def test_threshold_reached_triggers_close_and_resets_counter(self):
        gw = self._make_gateway()
        threshold = ModbusGateway._CONSECUTIVE_FAIL_THRESHOLD

        for _ in range(threshold):
            await gw.exchange(MagicMock())

        gw.client.close.assert_called_once()
        self.assertEqual(gw._consecutive_failures, 0)

    async def test_success_resets_failure_counter(self):
        mock_response = MagicMock(**{"isError.return_value": False})
        gw = self._make_gateway()

        # Two failures...
        for _ in range(2):
            await gw.exchange(MagicMock())
        self.assertEqual(gw._consecutive_failures, 2)

        # ...then a success
        gw.client.execute = AsyncMock(return_value=mock_response)
        result = await gw.exchange(MagicMock())
        self.assertIsNotNone(result)
        self.assertEqual(gw._consecutive_failures, 0)


# ---------------------------------------------------------------------------
# _poll_reachable_sensors — failed sensors show up as null, not missing keys
# ---------------------------------------------------------------------------
class TestPollReachableSensors(unittest.IsolatedAsyncioTestCase):
    def _make_server(self, sensors: dict):
        from hepic_server.hepic_server import PiServer

        server = PiServer.__new__(PiServer)
        server.config = {"sensor_timeout": 1.0}
        server.logger = MagicMock()
        server.sensor_name_by_id = {sid: f"Sensor {sid}" for sid in sensors}
        server.sensors = sensors
        return server

    async def test_all_sensors_present_in_payload(self):
        """Even failed sensors must appear in the payload (as null)."""
        from hepic_server.hepic_server import PiServer

        sensors = {
            "ok": MagicMock(get_value=AsyncMock(return_value=9.81)),
            "none": MagicMock(get_value=AsyncMock(return_value=None)),
            "error": MagicMock(get_value=AsyncMock(side_effect=RuntimeError("comm error"))),
        }
        server = self._make_server(sensors)
        payload = await server._poll_reachable_sensors()

        self.assertIn("Sensor ok", payload, "successful sensor must be in payload")
        self.assertEqual(payload["Sensor ok"], 9.81)

        self.assertIn("Sensor none", payload, "sensor returning None must be in payload")
        self.assertIsNone(payload["Sensor none"])

        self.assertIn("Sensor error", payload, "sensor raising exception must be in payload")
        self.assertIsNone(payload["Sensor error"])

    async def test_timeout_sensor_shows_as_null(self):
        async def hang():
            await asyncio.sleep(10)

        sensors = {
            "slow": MagicMock(get_value=AsyncMock(side_effect=hang)),
        }
        server = self._make_server(sensors)
        server.config["sensor_timeout"] = 0.05

        payload = await server._poll_reachable_sensors()

        self.assertIn("Sensor slow", payload)
        self.assertIsNone(payload["Sensor slow"])


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

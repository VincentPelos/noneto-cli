import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import requests

from noneto import diagnostics
from noneto.services.tidal import TimedSession


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.logger = logging.Logger('diagnostics-test')
        self.logger.propagate = False
        self.patch = patch.object(diagnostics, 'LOGGER', self.logger)
        self.patch.start()
        self.path = diagnostics.configure(self.temp.name)

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.patch.stop()
        self.temp.cleanup()

    def records(self):
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]

    def test_exception_chain_is_useful_without_secrets_or_raw_traceback(self):
        try:
            try:
                raise ConnectionResetError(10054, 'secret refresh_token=PRIVATE https://cdn.test/a?sig=SIGNED')
            except OSError as cause:
                raise requests.ConnectionError('Authorization: Bearer TOKEN') from cause
        except requests.ConnectionError as error:
            diagnostics.failure('stream.failed', error, track_id=3079103)
        record = self.records()[-1]
        self.assertEqual(record['category'], 'connection_reset')
        self.assertEqual(record['error_types'], ['ConnectionError', 'ConnectionResetError'])
        self.assertIn(10054, record['codes'])
        self.assertEqual(record['track_id'], 3079103)
        self.assertTrue(record['frames'])
        text = self.path.read_text()
        for value in ('PRIVATE', 'SIGNED', 'TOKEN', 'https://', 'Bearer', 'refresh_token'):
            self.assertNotIn(value, text)

    def test_http_diagnostics_omit_query_headers_and_oauth_body(self):
        response = Mock(status_code=200)
        with TimedSession() as client, patch.object(requests.Session, 'request', return_value=response):
            client.post('https://auth.tidal.com/v1/oauth2/token?private=QUERY_SECRET',
                        headers={'Authorization': 'Bearer HEADER_SECRET'},
                        data={'refresh_token': 'BODY_SECRET'})
        text = self.path.read_text()
        for secret in ('QUERY_SECRET', 'HEADER_SECRET', 'BODY_SECRET'):
            self.assertNotIn(secret, text)
        result = self.records()[-1]
        self.assertEqual(result['route'], '/v1/oauth2/token')
        self.assertEqual(result['status'], 200)
        self.assertIn('elapsed_ms', result)

    def test_http_connection_error_records_category_and_preserves_exception(self):
        error = requests.ConnectionError('getaddrinfo failed https://api.tidal.com/?token=SECRET')
        with TimedSession() as client, patch.object(requests.Session, 'request', side_effect=error):
            with self.assertRaises(requests.ConnectionError) as caught:
                client.get('https://api.tidal.com/v1/tracks/3079103/playbackinfopostpaywall')
        self.assertIs(caught.exception, error)
        record = self.records()[-1]
        self.assertEqual(record['event'], 'http.failed')
        self.assertEqual(record['category'], 'dns')
        self.assertNotIn('SECRET', self.path.read_text())

    def test_mpv_warnings_discard_signed_urls_and_arbitrary_prefix(self):
        diagnostics.mpv_message({'prefix': 'SECRET_PREFIX',
                                 'text': 'HTTP error 403 https://cdn.test/PRIVATE?signature=SECRET'})
        record = self.records()[-1]
        self.assertEqual(record['status'], 403)
        self.assertEqual(record['component'], 'mpv')
        self.assertEqual(record['category'], 'http')
        text = self.path.read_text()
        for secret in ('PRIVATE', 'SECRET', 'cdn.test'):
            self.assertNotIn(secret, text)

    def test_reconfiguration_closes_old_handler_and_rotation_is_bounded(self):
        previous = self.logger.handlers[0]
        diagnostics.configure(self.temp.name)
        self.assertIsNone(previous.stream)
        self.assertEqual(len(self.logger.handlers), 1)
        handler = self.logger.handlers[0]
        handler.maxBytes = 400
        for index in range(30):
            diagnostics.emit('track.selected', index=index)
        self.assertLessEqual(len(list(Path(self.temp.name).glob('noneto.log*'))), 3)
        self.assertTrue(Path(str(self.path) + '.1').exists())

    def test_sensitive_field_names_are_not_accepted(self):
        diagnostics.emit('test', token='SECRET', url='https://cdn.test/private', body='PRIVATE')
        self.assertNotIn('SECRET', self.path.read_text())
        self.assertNotIn('PRIVATE', self.path.read_text())
        self.assertNotIn('url', self.records()[-1])

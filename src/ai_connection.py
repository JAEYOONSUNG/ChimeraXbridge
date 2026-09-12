"""Explicit, session-only API setup with an off-thread authentication check."""
import json
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from Qt.QtCore import Qt, Signal
from Qt.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .backends import (clear_runtime_api_key, resolve_backend_api_key,
                       runtime_api_key_available, set_runtime_api_key, validate_api_key)
from .ui_theme import panel_stylesheet

_CHECK_SLOT = threading.BoundedSemaphore(1)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # Never forward a credential to another host, even on an API redirect.
        return None


def check_openai_connection(key, *, opener=None):
    """Read the model list; do not send prompts, scene data or generate tokens."""
    key = validate_api_key(key)
    request = Request('https://api.openai.com/v1/models',
                      headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'},
                      method='GET')
    transport = opener or build_opener(_NoRedirect())
    try:
        with transport.open(request, timeout=10) as response:
            data = response.read(1_048_577)
        if len(data) > 1_048_576:
            return False, 'OpenAI returned an unexpected response. Try again later.'
        decoded = json.loads(data.decode('utf-8'))
        if not isinstance(decoded, dict) or not isinstance(decoded.get('data'), list):
            return False, 'OpenAI returned an unexpected response. Check again later.'
        return True, ('Connected: model list received. Model access and usage limits '
                      'are checked when you run an AI request.')
    except HTTPError as error:
        code = error.code
        error.close()
        messages = {
            401: 'Key rejected. Check the key or create a new one in OpenAI Platform.',
            403: 'This key cannot read the model list. Check its project and permissions.',
            429: 'OpenAI is limiting requests. Check your API usage limits and retry later.',
        }
        return False, messages.get(code, f'OpenAI returned HTTP {code}. Retry later.')
    except (URLError, TimeoutError, OSError):
        return False, 'Could not reach OpenAI. Check the network or proxy and try again.'
    except (UnicodeError, ValueError):
        return False, 'OpenAI returned an unexpected response. Try again later.'
    except Exception:
        # Exception/response text can contain credentials; never echo it into UI/logs.
        return False, 'Connection check failed. Check the network and try again.'


class OpenAIConnectionDialog(QDialog):
    result_ready = Signal(int, bool, str)

    def __init__(self, parent=None, *, on_use=None, on_clear=None):
        super().__init__(parent)
        self._disposed = False
        self._busy = False
        self._generation = 0
        self._on_use, self._on_clear = on_use, on_clear
        self.setWindowTitle('Connect OpenAI API')
        self.setObjectName('codex_api_connection')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setStyleSheet(panel_stylesheet('codex_api_connection'))
        self.finished.connect(self._dispose)
        self.destroyed.connect(self._dispose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        intro = QLabel('Use your OpenAI Platform API key. API usage is billed separately '
                       'from a ChatGPT subscription; choose Codex CLI for subscription login.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(QLabel('API key'))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText('Paste your key; it stays in this app session only')
        self.key_edit.setAccessibleName('OpenAI API key')
        self.key_edit.textChanged.connect(self._edited)
        layout.addWidget(self.key_edit)
        note = QLabel('The key is kept only until ChimeraX exits. It is not saved to '
                      'files, shared profiles, logs, or the clipboard. Existing environment '
                      'keys are supported too.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(34)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.check_button = QPushButton('Check connection')
        self.check_button.clicked.connect(self._check)
        row.addWidget(self.check_button)
        self.use_button = QPushButton('Use key')
        self.use_button.clicked.connect(self._use)
        row.addWidget(self.use_button)
        self.clear_button = QPushButton('Clear session key')
        self.clear_button.clicked.connect(self._clear)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.check_button.setToolTip('Checks the model list only. No structure or AI prompt is sent.')
        self.clear_button.setToolTip('Removes the key entered here. An existing environment key remains available.')
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.result_ready.connect(self._checked, Qt.ConnectionType.QueuedConnection)
        self._show_source()
        self._update_buttons()
        self.resize(430, 285)

    def _dispose(self, *_args):
        self._disposed = True
        self._generation += 1

    def _candidate_key(self):
        return self.key_edit.text().strip() or resolve_backend_api_key('openai', strict=False)

    def _show_source(self):
        if runtime_api_key_available('openai'):
            message = 'A key is configured for this app session. You can check or replace it.'
        elif resolve_backend_api_key('openai', strict=False):
            message = 'An environment key is available. Check it or enter a session override.'
        else:
            message = 'Paste an API key to connect. Check connection sends only an authentication request.'
        self.status.setText(message)

    def _update_buttons(self):
        available = bool(self._candidate_key())
        self.check_button.setEnabled(available and not self._busy)
        self.use_button.setEnabled(available and not self._busy)
        self.clear_button.setEnabled(runtime_api_key_available('openai') and not self._busy)

    def _edited(self, *_args):
        if self._disposed:
            return
        self._generation += 1
        self.status.setText('Key changed. Check it, or use it for this app session.')
        self._update_buttons()

    def _use(self):
        if self._disposed or self._busy:
            return
        try:
            candidate = self._candidate_key()
            # Existing environment credentials need not be duplicated in memory.
            if self.key_edit.text().strip():
                set_runtime_api_key('openai', candidate)
            else:
                validate_api_key(candidate)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.key_edit.setText('')
        if self._on_use is not None:
            self._on_use()
        self.accept()

    def _clear(self):
        if self._disposed or self._busy:
            return
        clear_runtime_api_key('openai')
        self.key_edit.setText('')
        self._show_source()
        self._update_buttons()
        if self._on_clear is not None:
            self._on_clear()

    def _check(self):
        if self._disposed or self._busy:
            return
        try:
            key = validate_api_key(self._candidate_key())
        except ValueError as error:
            self.status.setText(str(error))
            return
        if not _CHECK_SLOT.acquire(blocking=False):
            self.status.setText('A connection check is still finishing. Try again shortly.')
            return
        self._busy = True
        generation = self._generation
        self.status.setText('Checking OpenAI…')
        self._update_buttons()
        def worker():
            try:
                ok, message = check_openai_connection(key)
            except Exception:
                ok, message = False, 'Connection check failed. Try again.'
            finally:
                _CHECK_SLOT.release()
            try:
                if not self._disposed:
                    self.result_ready.emit(generation, ok, message)
            except RuntimeError:
                pass  # The user closed/deleted this dialog during the request.
        try:
            threading.Thread(target=worker, name='ChimeraX API connection check', daemon=True).start()
        except Exception:
            _CHECK_SLOT.release()
            self._busy = False
            self.status.setText('Could not start the connection check. Try again.')
            self._update_buttons()

    def _checked(self, generation, ok, message):
        if self._disposed:
            return
        self._busy = False
        if generation == self._generation:
            self.status.setText(message)
        else:
            self.status.setText('Key changed during the check. Check the current key again.')
        self._update_buttons()

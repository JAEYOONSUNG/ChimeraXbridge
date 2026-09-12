"""Verify API setup with fake credentials, mocked HTTP and offscreen widgets only."""
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch, Mock
from urllib.error import HTTPError, URLError

assert not session.ui.is_gui
assert os.environ.get('QT_QPA_PLATFORM') == 'offscreen'
root=Path(__file__).resolve().parents[1]
app=runpy.run_path(str(root/'scripts/headless_ui_fixture.py'))['install'](session)
spec=importlib.util.spec_from_file_location('chimerax.codex_bridge',root/'src/__init__.py',submodule_search_locations=[str(root/'src')])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
package._schedule_helper_dock_layout=lambda *a,**k:None
from chimerax.codex_bridge import backends as b, ai_connection as ui
from chimerax.codex_bridge.tool import CodexAssistant
from Qt.QtCore import QTimer
from Qt.QtWidgets import QApplication, QLineEdit
from PyQt6.QtTest import QTest
from PyQt6 import sip

secret='test_'+'private-session-token'
environment_key='test_'+'environment-token'
old_keys=dict(b._runtime_api_keys)
b._runtime_api_keys.clear()
original_environ=dict(os.environ)
clipboard=app.clipboard();clipboard.setText('offscreen clipboard sentinel')


def wait_for(predicate, seconds=3):
    start=time.monotonic()
    while not predicate() and time.monotonic()-start<seconds:QTest.qWait(5)
    assert predicate(), 'Timed out waiting for isolated connection test'


class Response(io.BytesIO):
    pass


class Transport:
    def __init__(self,payload=None,error=None):self.payload=payload;self.error=error;self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout))
        if self.error:raise self.error
        return Response(self.payload)


with patch.dict(os.environ,{'OPENAI_API_KEY':environment_key},clear=False):
    for name in ('CODEX_BRIDGE_OPENAI_API_KEY',):os.environ.pop(name,None)
    before=dict(os.environ)
    assert b.resolve_backend_api_key('openai')==environment_key
    b.set_runtime_api_key('openai',secret)
    assert b.resolve_backend_api_key('openai')==secret and dict(os.environ)==before
    assert b.backend_availability('openai')[1]=='key set'
    assert secret not in repr(b.backend_availability('openai'))
    for invalid in ('',None,'has spaces','two\nlines','nul'+chr(0),'bad'+chr(127),'한글'):
        try:b.set_runtime_api_key('openai',invalid)
        except ValueError as e:assert secret not in str(e)
        else:raise AssertionError('Invalid key accepted')
    try:b.set_runtime_api_key('codex',secret)
    except ValueError:pass
    else:raise AssertionError('API key incorrectly attached to CLI subscription')
    b.clear_runtime_api_key('openai')
    assert b.resolve_backend_api_key('openai')==environment_key

    transport=Transport(b'{"data": [{"id": "fixture-model"}]}')
    ok,message=ui.check_openai_connection(secret,opener=transport)
    assert ok and secret not in message
    request,timeout=transport.calls[0]
    assert request.full_url=='https://api.openai.com/v1/models'
    assert request.method=='GET' and request.data is None and timeout==10
    assert request.get_header('Authorization')=='Bearer '+secret
    assert ui._NoRedirect().redirect_request(request,None,302,'redirect',{},'https://example.invalid') is None
    for code in (301,401,403,429,500):
        error=HTTPError(request.full_url,code,secret,{},io.BytesIO(secret.encode()))
        ok,message=ui.check_openai_connection(secret,opener=Transport(error=error))
        assert not ok and secret not in message
    for failure in (URLError(secret),TimeoutError(secret),RuntimeError(secret)):
        ok,message=ui.check_openai_connection(secret,opener=Transport(error=failure))
        assert not ok and secret not in message
    for payload in (b'not json',b'{"unexpected":1}',b'['+b' '*1_048_577+b']'):
        ok,message=ui.check_openai_connection(secret,opener=Transport(payload))
        assert not ok and secret not in message

    # Model-request failures must not copy a server-echoed key into app logs.
    import traceback
    from chimerax.codex_bridge import service
    for failure in (HTTPError('https://api.openai.com/v1/responses',401,'invalid',{},io.BytesIO(secret.encode())), URLError(secret)):
        with patch.object(service,'urlopen',side_effect=failure):
            try:service._openai_create_response(api_key=secret,model='fixture-model',reasoning=None,input_items=[])
            except service.CodexBridgeError as error:
                assert secret not in str(error) and secret not in traceback.format_exc()
                assert '[redacted]' in str(error)
            else:raise AssertionError('Simulated API error was ignored')

    # Closing a draft never saves/validates it; no HTTP call occurs on construction/use.
    used=[];cleared=[]
    dialog=ui.OpenAIConnectionDialog(on_use=lambda:used.append(True),on_clear=lambda:cleared.append(True))
    assert dialog.key_edit.echoMode()==QLineEdit.EchoMode.Password and dialog.key_edit.text()==''
    assert environment_key not in dialog.status.text()
    with patch.object(ui,'check_openai_connection',side_effect=AssertionError('Unexpected automatic network request')):
        dialog.key_edit.setText(secret)
        dialog.reject();QTest.qWait(5)
        assert not b.runtime_api_key_available('openai')
        dialog=ui.OpenAIConnectionDialog(on_use=lambda:used.append(True),on_clear=lambda:cleared.append(True))
        dialog.key_edit.setText(secret);dialog.use_button.click();QTest.qWait(5)
    assert used==[True] and b.resolve_backend_api_key('openai')==secret
    assert dict(os.environ)==before and clipboard.text()=='offscreen clipboard sentinel'
    dialog=ui.OpenAIConnectionDialog(on_clear=lambda:cleared.append(True))
    assert dialog.key_edit.text()=='' and secret not in dialog.status.text()
    dialog.clear_button.click()
    assert cleared==[True] and b.resolve_backend_api_key('openai')==environment_key
    assert 'environment' in dialog.status.text().lower()

    # An explicit Check is asynchronous and bounded; it does not adopt the draft key.
    entered=threading.Event();release=threading.Event();calls=[]
    main_thread=threading.get_ident()
    def slow_check(key):
        assert threading.get_ident()!=main_thread
        calls.append(key);entered.set();assert release.wait(2)
        return True,'Connected: fixture response'
    dialog.key_edit.setText(secret)
    ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(5)
    with patch.object(ui,'check_openai_connection',side_effect=slow_check):
        started=time.monotonic();dialog.check_button.click()
        assert time.monotonic()-started<.2
        wait_for(entered.is_set);QTest.qWait(30)
        dialog._check();assert len(calls)==1 and len(ticks)>=2
        dialog.key_edit.setText('test_'+'changed-draft')
        release.set();wait_for(lambda:not dialog._busy)
        assert 'changed' in dialog.status.text().lower()
    timer.stop()
    assert b.resolve_backend_api_key('openai')==environment_key
    # Thread-start failures release the concurrency guard for the next attempt.
    with patch.object(ui.threading.Thread,'start',side_effect=RuntimeError(secret)):
        dialog._check()
    assert not dialog._busy and secret not in dialog.status.text()
    assert ui._CHECK_SLOT.acquire(False);ui._CHECK_SLOT.release()

    entered.clear();release.clear();calls.clear()
    with patch.object(ui,'check_openai_connection',side_effect=slow_check):
        dialog._check();wait_for(entered.is_set)
        other=ui.OpenAIConnectionDialog()
        other._check();assert 'finishing' in other.status.text()
        dialog.reject();sip.delete(dialog) if not sip.isdeleted(dialog) else None
        release.set();QTest.qWait(40)
        assert len(calls)==1
        other.reject();QTest.qWait(5)
    assert ui._CHECK_SLOT.acquire(False);ui._CHECK_SLOT.release()
    assert clipboard.text()=='offscreen clipboard sentinel'

    # The Assistant exposes actual settings and uses the entered key for OpenAI.
    assistant=CodexAssistant(session,'AI Assistant')
    with patch.object(ui,'check_openai_connection',side_effect=AssertionError('Unexpected automatic network request')):
        assistant._setup_backend('openai')
        connection=assistant._api_setup_dialog
        connection.key_edit.setText(secret);connection.use_button.click();QTest.qWait(5)
    assert b.get_current_backend_id(session)=='openai' and b.resolve_backend_api_key('openai')==secret
    assert secret not in assistant.terminal_edit.toPlainText()
    # Login commands only: never start Terminal/browser/auth in this test.
    for backend,args in (('codex','login'),('claude','auth login'),('gemini','')):
        with patch('chimerax.codex_bridge.tool.resolve_backend_cli',return_value='/tmp/test-cli'), \
             patch('chimerax.codex_bridge.tool.subprocess.Popen') as start, \
             patch('chimerax.codex_bridge.tool.sys.platform','darwin'):
            assistant._setup_backend(backend)
            command=start.call_args.args[0]
            assert command[:2]==['osascript','-e'] and '/tmp/test-cli'+(' '+args if args else '') in command[2]
            assert secret not in repr(command)
    with patch('chimerax.codex_bridge.tool.resolve_backend_cli',return_value='/tmp/test-cli'), \
         patch('chimerax.codex_bridge.tool.subprocess.Popen') as start, \
         patch('chimerax.codex_bridge.tool.sys.platform','linux'), \
         patch.object(assistant,'_copy_text_to_clipboard') as copied:
        assistant._setup_backend('claude');start.assert_not_called()
        assert copied.call_args.args[0]=='/tmp/test-cli auth login'
    assistant._setup_backend('openai')
    connection=assistant._api_setup_dialog
    connection.show();QTest.qWait(10)
    assert connection.grab().save('/tmp/openai-connection-settings.png')
    connection.reject();assistant.delete();sip.delete(assistant.tool_window.ui_area)
    QTest.qWait(10)

b._runtime_api_keys.clear();b._runtime_api_keys.update(old_keys)
assert dict(os.environ)==original_environ
print('AI_CONNECTION_OK: masked session-only keys, no automatic network/clipboard/disk writes, explicit background check, bounded close handling, CLI login routes')

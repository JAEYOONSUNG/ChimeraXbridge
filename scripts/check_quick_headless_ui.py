"""Exercise real quick runtime/results widgets using offscreen-only test hosts."""
from pathlib import Path
import runpy

scripts = Path(__file__).resolve().parent
fixture = runpy.run_path(str(scripts / 'headless_ui_fixture.py'))
fixture['install'](session)
runpy.run_path(str(scripts / 'check_quick_refinement.py'), init_globals={'session': session})
# Both inherited checks expect their own empty fixture scene.
session.selection.clear()
session.models.close(session.models.list())
runpy.run_path(str(scripts / 'check_quick_runtime.py'), init_globals={'session': session})
print('QUICK_HEADLESS_UI_OK: comparison, exports, cache, cancellation, observer and responsiveness')

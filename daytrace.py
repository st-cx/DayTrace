"""Backward-compatible entry point; implementation lives in separate modules."""
import sys
import daytrace_api as _application
if __name__ == '__main__':
    _application.main()
else:
    sys.modules[__name__] = _application

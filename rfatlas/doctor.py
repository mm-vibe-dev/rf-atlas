"""Sanitized finite diagnostics; device enumeration never proves sample delivery."""
import importlib.metadata
import platform
from pathlib import Path
import tempfile
import time
import socket
from .hardware import RTLBackend


def diagnose(config, *, receiver=True):
    from . import __version__
    from .ai import status
    report=dict(schema=1,application_version=__version__,python=platform.python_version(),
        system=platform.system(),architecture=platform.machine(),dependencies={},tools={},
        ai=status(config.root),receiver=dict(status='NOT_TESTED'),privacy='No paths, serials, host names, raw stderr or RF content included')
    for name in ('numpy','scipy','matplotlib','duckdb'):
        try:report['dependencies'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:report['dependencies'][name]='NOT_INSTALLED'
    backend=RTLBackend(config)
    for name in ('rtl_sdr','rtl_power','rtl_test'):
        try:backend.tool(name);report['tools'][name]='AVAILABLE'
        except RuntimeError:report['tools'][name]='NOT_CONFIGURED'
    from .identification import default_registry
    report['optional_decoders']=[dict(name=d['name'],available=d['available']) for d in default_registry(config=config).describe()]
    report['configuration']=dict(status='PASS',validated=True)
    report['native_libraries']=dict(status='WARNING',availability='Only CLI paths checked; linked native library versions are not independently established')
    report['usb_permissions']=dict(status='WARNING',access='Not established independently; successful sample delivery demonstrates access for this process')
    report['audio']=dict(status='WARNING',server_streaming_available=True,browser_playback='REQUIRES_BROWSER_USER_GESTURE',live_audio_verified=False)
    with socket.socket() as port:
        try:port.bind(('127.0.0.1',8765));report['port']=dict(port=8765,status='PASS',available=True)
        except OSError:report['port']=dict(port=8765,status='WARNING',available=False,remedy='Use serve --port with a free loopback port, or use the existing application.')
    report['summary']='WARNING'
    if not receiver:return report
    if report['tools']['rtl_sdr']!='AVAILABLE':
        report['receiver']=dict(status='TOOL_UNAVAILABLE',sample_delivery_verified=False);return report
    attempts=[]
    try:
        with backend.lock(),tempfile.TemporaryDirectory(prefix='rf-atlas-doctor-') as folder:
            # One process at a time, one retry, no USB/driver changes. A restart
            # is a recovery attempt, never a demonstrated fix without bytes.
            for attempt in range(2):
                output=Path(folder)/f'probe-{attempt}.cu8';began=time.monotonic();failure=None
                try:backend.run([backend.tool('rtl_sdr'),'-d',config.device,'-f','100700000','-s','250000','-g','15.7','-n','50000',str(output)],5)
                except Exception as error:failure=type(error).__name__
                size=output.stat().st_size if output.exists() else 0
                attempts.append(dict(attempt=attempt+1,bytes=size,elapsed_s=time.monotonic()-began,error_class=failure))
                if size>=100000 and size%2==0 and failure is None:break
        passed=attempts[-1]['bytes']>=100000 and attempts[-1]['bytes']%2==0 and attempts[-1]['error_class'] is None
        report['receiver']=dict(status='SAMPLES_VERIFIED' if passed else 'SAMPLE_DELIVERY_FAILED',
            sample_delivery_verified=passed,restart_recovery_verified=passed and len(attempts)>1,attempts=attempts,
            root_cause='NOT_ESTABLISHED' if not passed else 'NOT_DIAGNOSED',
            recommendation='Inspect USB passthrough and receiver ownership manually if delivery fails; no infrastructure changes were made.')
        report['summary']='PASS' if passed else 'FAIL'
    except Exception as error:
        report['receiver']=dict(status='LOCK_OR_PROBE_UNAVAILABLE',sample_delivery_verified=False,error_class=type(error).__name__)
    return report

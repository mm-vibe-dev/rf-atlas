"""Validated portable configuration; no machine-specific defaults."""
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
import os
import tomllib


def default_root():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'RF-Atlas'
    return Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'rf-atlas'


def resolve_path(value, base):
    expanded=os.path.expandvars(os.path.expanduser(str(value)))
    if os.name!='nt' and PureWindowsPath(expanded).is_absolute():
        raise ValueError('Windows absolute path supplied on POSIX; use a native configuration')
    path=Path(expanded)
    return (path if path.is_absolute() else base/path).resolve()


@dataclass(frozen=True)
class Config:
    root: Path
    device: str = '0'
    tools: dict = field(default_factory=dict)
    lock_root: Path = field(default_factory=lambda: default_root().parent/'rf-atlas-locks')
    max_capture_bytes: int = 64*1024*1024
    retention_bytes: int = 512*1024*1024
    survey_start_hz: int = 100000000
    survey_stop_hz: int = 101000000
    decoders: dict = field(default_factory=dict)


def load_config(filename=None):
    value=filename or os.environ.get('RF_ATLAS_CONFIG')
    if value is None and Path('config.local.toml').is_file(): value='config.local.toml'
    data={};base=Path.cwd()
    if value:
        path=Path(value).expanduser().resolve();base=path.parent
        with path.open('rb') as file: data=tomllib.load(file)
    if set(data)-{'runtime','hardware','tools','survey','decoders'}: raise ValueError('Unknown configuration section')
    runtime=data.get('runtime',{});hardware=data.get('hardware',{});tools=data.get('tools',{})
    survey=data.get('survey',{})
    if set(runtime)-{'root','max_capture_bytes','retention_bytes'} or set(hardware)-{'device'} or set(tools)-{'rtl_power','rtl_sdr','rtl_test'}:
        raise ValueError('Unknown configuration key; decoder and transmission options are unavailable')
    if set(survey)-{'start_hz','stop_hz'}: raise ValueError('Unknown survey setting')
    start=survey.get('start_hz',100000000);stop=survey.get('stop_hz',101000000)
    if type(start) is not int or type(stop) is not int or not 500000<=start<stop<=1766000000:
        raise ValueError('Invalid configured survey range')
    root=resolve_path(os.environ.get('RF_ATLAS_HOME',runtime.get('root',str(default_root()))),base)
    selector=str(hardware.get('device','0'))
    if not selector or len(selector)>128 or any(c in selector for c in '\r\n\x00'):
        raise ValueError('Invalid device selector')
    max_bytes=runtime.get('max_capture_bytes',64*1024*1024);retention=runtime.get('retention_bytes',512*1024*1024)
    if type(max_bytes) is not int or type(retention) is not int or not 8192<=max_bytes<=retention:
        raise ValueError('Capture budgets require 8192 <= max_capture_bytes <= retention_bytes')
    decoder_paths={}
    optional=data.get('decoders',{})
    if not isinstance(optional,dict) or set(optional)-{'rtl_433','dsd_fme'}:raise ValueError('Unknown optional decoder setting')
    for name,value in optional.items():
        if not isinstance(value,str) or not value:raise ValueError('Decoder must be an executable path or PATH name')
        decoder_paths[name]=str(resolve_path(value,base)) if '/' in value or '\\' in value else value
    resolved={}
    for name,value in tools.items():
        if not isinstance(value,str) or not value: raise ValueError('Tool must be an executable path or PATH name')
        resolved[name]=str(resolve_path(value,base)) if '/' in value or '\\' in value else value
    lock_root=resolve_path(os.environ['RF_ATLAS_LOCK_ROOT'],base) if os.environ.get('RF_ATLAS_LOCK_ROOT') else default_root().parent/'rf-atlas-locks'
    return Config(root,selector,resolved,lock_root=lock_root,max_capture_bytes=max_bytes,retention_bytes=retention,survey_start_hz=start,survey_stop_hz=stop,decoders=decoder_paths)

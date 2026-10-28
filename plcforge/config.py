from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .security import register_secret

SETTINGS_KEYS = {'base_url','model','api_key','api_key_env','max_output_tokens',
                 'timeout_seconds','max_retries','retry_base_seconds'}


def application_home():
    return Path(os.environ.get('PLCFORGE_HOME',str(Path.home()/'.plcforge'))).expanduser().resolve()


def normalize_base_url(value):
    if not isinstance(value,str) or not value.strip() or any(ord(c)<32 for c in value):
        raise ValueError('base_url must be a nonempty HTTP(S) URL')
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('https','http') and parsed.hostname and parsed.port != 0
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('base_url must be HTTP(S), without URL credentials, query or fragment')
    if value.endswith('/chat/completions'):
        value = value[:-len('/chat/completions')]
    return value


def read_object(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise ValueError('Invalid JSON in %s at line %d, column %d' % (path,exc.lineno,exc.colno)) from None
    if not isinstance(data,dict):
        raise ValueError('Configuration must be a JSON object: '+str(path))
    return data


def load_env(path: Path) -> None:
    """A deliberately small .env reader; never executes shell expressions."""
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


@dataclass
class Config:
    base_url: str = 'https://api-inference.modelscope.cn/v1'
    model: str = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
    api_key_env: str = 'MODELSCOPE_API_KEY'
    api_key: str = field(default='',repr=False)
    _settings_path: str = field(default='',init=False,repr=False)
    max_turns: int = 24
    max_output_tokens: int = 8192
    total_token_budget: int = 160000
    request_token_budget: int = 0
    min_output_tokens: int = 256
    input_estimate_safety_factor: float = 1.2
    context_chars: int = 80000
    timeout_seconds: int = 120
    wall_seconds: int = 900
    max_repeated_results: int = 4
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    codesys: dict = field(default_factory=lambda: {
        'executable': '', 'profile': '', 'additionalfolder': '',
        'template_project': '', 'application': 'Application',
        'timeout_seconds': 180, 'generate_code': True,
    })

    @classmethod
    def load(cls, path: Path | None = None, *, apply_env=True) -> 'Config':
        data = read_object(path) if path and path.is_file() else {}
        if any(k not in cls.__dataclass_fields__ or k.startswith('_') for k in data):
            raise ValueError('Unknown field in runtime configuration')
        obj = cls(**data)
        if apply_env:
            obj.apply_env()
        return obj.validate()

    def __post_init__(self):
        if isinstance(self.api_key,str):
            register_secret(self.api_key)
        if isinstance(self.api_key_env,str):
            register_secret(os.environ.get(self.api_key_env,''))

    def apply_env(self):
        if 'PLCFORGE_BASE_URL' in os.environ:
            selected = normalize_base_url(os.environ['PLCFORGE_BASE_URL'])
            if selected != normalize_base_url(self.base_url):
                self.api_key, self.api_key_env = '', 'PLCFORGE_API_KEY'
            self.base_url = selected
        self.model = os.environ.get('PLCFORGE_MODEL',self.model)

    def validate(self):
        self.base_url = normalize_base_url(self.base_url)
        if not isinstance(self.model,str) or not self.model.strip() or any(ord(c)<32 for c in self.model):
            raise ValueError('model must be a nonempty model ID')
        self.model = self.model.strip()
        if not isinstance(self.api_key,str) or any(c in self.api_key for c in '\r\n'):
            raise ValueError('api_key must be a single-line string')
        if self.api_key and len(self.api_key)<8:
            raise ValueError('api_key must have at least 8 characters; for a local unauthenticated API use api_key_env=""')
        register_secret(self.api_key)
        if not isinstance(self.api_key_env,str) or (self.api_key_env and not re.fullmatch(r'[A-Za-z_]\w*',self.api_key_env)):
            raise ValueError('api_key_env must be an environment-variable name, or empty for no authentication')
        register_secret(os.environ.get(self.api_key_env,''))
        for key in ('max_turns', 'max_output_tokens', 'total_token_budget', 'context_chars',
                    'timeout_seconds', 'wall_seconds', 'max_repeated_results', 'min_output_tokens'):
            if type(getattr(self,key)) is not int or getattr(self,key) < 1:
                raise ValueError('%s must be positive' % key)
        if type(self.request_token_budget) is not int or self.request_token_budget < 0:
            raise ValueError('request_token_budget must be nonnegative (0 means no extra per-request limit)')
        if type(self.input_estimate_safety_factor) not in (float,int) or not math.isfinite(self.input_estimate_safety_factor) or self.input_estimate_safety_factor < 1:
            raise ValueError('input_estimate_safety_factor must be at least 1')
        if type(self.max_retries) is not int or self.max_retries < 0 or type(self.retry_base_seconds) not in (int,float) or self.retry_base_seconds <= 0:
            raise ValueError('max_retries must be nonnegative; retry_base_seconds must be positive')
        return self

    def public(self) -> dict:
        return {k:v for k,v in asdict(self).items() if k!='api_key' and not k.startswith('_')}

    def describe(self):
        return {'settings_file':self._settings_path or None, 'base_url':self.base_url,'model':self.model,
                'api_key_env':self.api_key_env,'credential_present':self.has_key(),
                'authentication':'bearer' if self.api_key or self.api_key_env else 'none',
                'credential_source':'settings api_key' if self.api_key else self.api_key_env or 'none'}

    def has_key(self):
        return bool(self.api_key or (self.api_key_env and os.environ.get(self.api_key_env)))

    def key(self) -> str:
        if self.api_key:
            register_secret(self.api_key)
            return self.api_key
        if not self.api_key_env:
            return ''
        key = os.environ.get(self.api_key_env, '')
        if not key:
            raise ValueError('Missing %s. Set it in the environment or fill api_key in settings.json.' % self.api_key_env)
        register_secret(key)
        return key


def load_runtime_config(config_path, project_root, workspace, settings_path=None, *, user_home=None):
    config = Config.load(config_path,apply_env=False)
    selected = None
    if settings_path is not None:
        selected = Path(settings_path).expanduser().resolve()
        if not selected.is_file():
            raise ValueError('Settings file does not exist: '+str(selected))
    else:
        user_directory = Path(user_home)/'.plcforge' if user_home is not None else application_home()
        candidates = [Path(workspace)/'.plcforge/settings.json',user_directory/'settings.json',Path(project_root)/'settings.json']
        selected = next((p.resolve() for p in candidates if p.is_file()),None)
    if selected:
        data = read_object(selected)
        if any(key not in SETTINGS_KEYS for key in data):
            raise ValueError('Unknown field in settings.json; allowed: '+', '.join(sorted(SETTINGS_KEYS)))
        # A provider file owns its credentials; never inherit another endpoint's token.
        config.api_key = ''
        config.api_key_env = 'PLCFORGE_API_KEY'
        for key,value in data.items():
            setattr(config,key,value)
        config._settings_path = str(selected)
        config.validate()
    config.apply_env()
    return config.validate()


def settings_template(config=None):
    config = config or Config()
    return {'base_url':config.base_url,'model':config.model,'api_key':'',
            'api_key_env':'PLCFORGE_API_KEY' if config.api_key else config.api_key_env}

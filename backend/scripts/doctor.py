"""Read-only local readiness report. Never prints keys or sends API requests."""

import argparse
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config, credential_present


def report():
    checks = [{'name': 'Python 3.11 / 3.12', 'passed': (3, 11) <= sys.version_info[:2] < (3, 13), 'detail': platform.python_version()}]
    for package in ('flask', 'openai', 'zep-cloud', 'camel-oasis', 'camel-ai', 'pytest'):
        try:
            version = importlib.metadata.version(package)
            checks.append({'name': package, 'passed': True, 'detail': version})
        except importlib.metadata.PackageNotFoundError:
            checks.append({'name': package, 'passed': False, 'detail': '未安装'})
    root = Path(__file__).resolve().parents[2]
    for label, path in [('前端依赖', root / 'frontend' / 'node_modules'), ('根目录依赖', root / 'node_modules')]:
        checks.append({'name': label, 'passed': path.is_dir(), 'detail': str(path)})
    keys = {'llm_key_present': credential_present(Config.LLM_API_KEY), 'zep_key_present': credential_present(Config.ZEP_API_KEY)}
    return {'environment_ready': all(item['passed'] for item in checks), 'checks': checks,
            'model_configuration': keys, 'live_configuration_present': all(keys.values()),
            'model': Config.LLM_MODEL_NAME, 'research_only_environment': os.environ.get('MIROFISH_RESEARCH_ONLY') == '1',
            'notes': ['仅检查本地环境及凭据是否存在，不验证凭据有效性、额度或供应商连接。',
                      '未发起模型、Zep 或其他网络请求；真实预测能力须通过独立真实数据检验。']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-live', action='store_true')
    args = parser.parse_args()
    result = report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not result['environment_ready'] or (args.require_live and not result['live_configuration_present']))


if __name__ == '__main__':
    raise SystemExit(main())

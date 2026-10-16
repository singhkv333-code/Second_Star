import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('metadata', Path(__file__).with_name('repair_metadata_routes.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_preserves_both_hosts_and_api_fallback():
    source = Path(__file__).with_name('nginx-charto.conf').read_text().replace(m.BLOCK, '')
    for host in ('pivot-india.centralindia.cloudapp.azure.com', 'app.pivotnow.in'):
        original = source.replace('pivot-india.centralindia.cloudapp.azure.com', host)
        result = m.render(original)
        assert result.replace(m.BLOCK, '') == original
        assert m.render(result) == result
        assert result.index(m.BLOCK) < result.index("    # The shell's real pages.")


def test_refuses_unfamiliar_or_conflicting_config():
    with pytest.raises(ValueError):
        m.render('server { location / { return 200; } }')
    source = Path(__file__).with_name('nginx-charto.conf').read_text().replace(m.BLOCK, '')
    with pytest.raises(ValueError):
        m.render(source + '\nlocation = /icon.svg { return 404; }')


def test_failed_nginx_validation_restores_both_vhosts(tmp_path, monkeypatch):
    import subprocess
    import sys
    real_path = Path
    source = real_path(__file__).with_name('nginx-charto.conf').read_text().replace(m.BLOCK, '')
    site_dir = tmp_path / 'etc/nginx/sites-available'
    site_dir.mkdir(parents=True)
    for name, _ in m.SITES:
        (site_dir / name).write_text(source)
    monkeypatch.setattr(m, 'Path', lambda value: tmp_path / str(value).lstrip('/'))
    monkeypatch.setattr(sys, 'argv', ['repair_metadata_routes.py', '--apply'])
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(m.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        m.main()
    for name, _ in m.SITES:
        assert (site_dir / name).read_text() == source
    assert calls == [['nginx', '-t'], ['nginx', '-t'], ['systemctl', 'reload', 'nginx']]

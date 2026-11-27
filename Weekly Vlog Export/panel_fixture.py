"""Explicit path bounds for the disposable native panel pilot."""
import os
from pathlib import Path

PILOTS = Path(__file__).resolve().parent.parent / 'interface' / 'Runs'


def allowed(root):
    return root.parent.parent == PILOTS and root.name == 'VlogSync' and root.parent.name in (
        'Native panel pilot', 'Native panel pilot 2', 'Native panel pilot 3')


def workspace():
    value = os.environ.get('VLOGSYNC_DISPOSABLE_NATIVE')
    if value is None:
        return None
    root = Path(value).resolve()
    if not allowed(root) or not root.is_dir():
        raise RuntimeError('Expected the exact isolated native pilot workspace.')
    for child in ('VlogSync', 'Camera01', 'Extra Footage', '1080p Master Vlogs',
                  '720p Editor Proxies', 'Edited Videos', 'Scratch'):
        path = root.parent / child
        if path.resolve()!=path or not path.is_dir():
            raise RuntimeError('The native pilot directory is missing or redirected.')
    return root

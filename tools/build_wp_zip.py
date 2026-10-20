"""Build the WordPress plugin zip: dist/inventree-shop-X.Y.Z.zip, ready for Plugins -> Add New -> Upload.

    python tools/build_wp_zip.py            # build it
    python tools/build_wp_zip.py --check    # only check the WordPress version matches version.py (CI)

The zip holds one folder, inventree-shop/, with forward-slash paths (WordPress on Linux needs
that - PowerShell's Compress-Archive writes backslashes, which is why this is Python).
"""

import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / 'wordpress' / 'inventree-shop'
SKIP = {'.DS_Store', 'Thumbs.db'}


def python_version():
    text = (ROOT / 'inventree_woocommerce' / 'version.py').read_text(encoding='utf-8')
    return re.search(r'PLUGIN_VERSION\s*=\s*["\'](.+?)["\']', text).group(1)


def wordpress_versions():
    """The three places the WordPress plugin states its version."""
    main = (PLUGIN / 'inventree-shop.php').read_text(encoding='utf-8')
    readme = (PLUGIN / 'readme.txt').read_text(encoding='utf-8')
    return {
        'inventree-shop.php header': re.search(r'^\s*\*\s*Version:\s*(\S+)', main, re.M).group(1),
        'INVENTREE_SHOP_VERSION': re.search(r"define\(\s*'INVENTREE_SHOP_VERSION',\s*'([^']+)'", main).group(1),
        'readme.txt Stable tag': re.search(r'^Stable tag:\s*(\S+)', readme, re.M).group(1),
    }


def check():
    version = python_version()
    wrong = {where: v for where, v in wordpress_versions().items() if v != version}
    for where, v in wrong.items():
        print(f'{where} says {v}, version.py says {version}')
    if wrong:
        raise SystemExit(1)
    return version


def build():
    version = check()
    out = ROOT / 'dist' / f'inventree-shop-{version}.zip'
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN.rglob('*')):
            if path.is_file() and path.name not in SKIP and '__pycache__' not in path.parts:
                zf.write(path, 'inventree-shop/' + path.relative_to(PLUGIN).as_posix())
    print(f'wrote {out.relative_to(ROOT).as_posix()}')
    return out


if __name__ == '__main__':
    if '--check' in sys.argv:
        print(f'WordPress plugin version matches: {check()}')
    else:
        build()

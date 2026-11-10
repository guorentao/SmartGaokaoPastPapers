#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 用：校验试卷打包下载的 zip 是否完好。

用法: python scripts/ci_check_zip.py /tmp/math.zip
"""

import sys
import zipfile


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/math.zip'
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if not names:
            print('✗ 压缩包为空')
            return 1
        bad = z.testzip()
        if bad is not None:
            print(f'✗ 压缩包内文件损坏: {bad}')
            return 1
        sizes = z.infolist()
        total = sum(i.file_size for i in sizes)
        print(f'✓ {len(names)} 个文件，解压后 {total / 1024 / 1024:.1f} MB')
        print('  示例: ' + ', '.join(names[:3]))
    return 0


if __name__ == '__main__':
    sys.exit(main())

import os
import shutil
from pathlib import Path

src_dir = Path('/Users/antongotskyi/клод джоб/server-copy/app')
dst_dir = Path('/Users/antongotskyi/клод джоб/Pipka/app')

def copy_files():
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            if not f.endswith('.py'):
                continue
            src_file = Path(root) / f
            rel_path = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel_path
            
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            print(f"Copying {rel_path}")
            shutil.copy2(src_file, dst_file)
            
if __name__ == "__main__":
    copy_files()

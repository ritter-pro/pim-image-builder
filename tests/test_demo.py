from pathlib import Path
import tempfile
import unittest
from PIL import Image
from pim_engine import build
ROOT = Path(__file__).resolve().parents[1]

class DemoTest(unittest.TestCase):
    def test_build_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(table=str(ROOT/'demo/catalog_demo.xlsx'),
                       pdfs=str(ROOT/'demo/pdfs'), packs=str(ROOT/'demo/packs'),
                       drawings=str(ROOT/'demo/drawings'), output=tmp, cylinder='')
            result = build(cfg)
            self.assertEqual((result['complete'], result['partial'], result['errors']), (1, 0, 0))
            folder = Path(result['output'])/'9990000000001_2027DEMO-0001'
            files = list(folder.glob('*.png'))
            self.assertEqual(len(files), 7)
            for file in files:
                with Image.open(file) as im:
                    self.assertEqual(im.size, (3000, 3000))
                    im.verify()
            self.assertEqual((folder/'0001-8.png').read_bytes(), (ROOT/'demo/packs/9990000000001_2027DEMO-0001.png').read_bytes())
            with Image.open(folder/'0001-2.png') as im:
                self.assertEqual(im.mode, 'RGBA')
                self.assertEqual(im.getpixel((0, 0))[3], 0)
            before = {p.name: p.stat().st_mtime_ns for p in files}
            self.assertEqual(build(cfg)['skipped'], 1)
            self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in files})

if __name__ == '__main__':
    unittest.main()

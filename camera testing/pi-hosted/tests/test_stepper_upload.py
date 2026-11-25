"""Artifact and board selection checks; no USB device is opened or programmed."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stepper_upload',ROOT/'tools/upload-stepper-firmware.py')
upload=importlib.util.module_from_spec(spec)
spec.loader.exec_module(upload)


class UploadSelectionTests(unittest.TestCase):
    def test_each_prepared_image_matches_its_original_motor_board_and_sources(self):
        for axis,serial in upload.MOTOR_SERIALS.items():
            with self.subTest(axis=axis):
                manifest,target,image=upload.prepared_target(axis,serial)
                self.assertEqual(image.name,axis+'.hex')
                self.assertEqual(target['usbSerial'],serial)
                self.assertEqual(manifest['layout'],'separate-motor-nanos')

    def test_swapped_motor_boards_and_pulley_cannot_be_selected(self):
        for axis in ('X','Y'):
            for serial in (upload.MOTOR_SERIALS['Y' if axis=='X' else 'X'],upload.PULLEY_SERIAL,'unknown'):
                with self.subTest(axis=axis,serial=serial),self.assertRaises(SystemExit):
                    upload.prepared_target(axis,serial)
        with self.assertRaises(SystemExit):
            upload.prepared_target('XY',upload.MOTOR_SERIALS['Y'])

    def copied_artifacts(self):
        directory=tempfile.TemporaryDirectory()
        source=Path(directory.name)/'firmware'
        shutil.copytree(upload.IMAGES.parent,source)
        self.addCleanup(directory.cleanup)
        patcher=patch.object(upload,'IMAGES',source/'hex')
        patcher.start();self.addCleanup(patcher.stop)
        return source

    def test_changed_firmware_image_is_rejected_before_programming(self):
        source=self.copied_artifacts()
        (source/'hex/X.hex').write_bytes(b'not the prepared image')
        with self.assertRaisesRegex(SystemExit,'checksum'):
            upload.prepared_target('X',upload.MOTOR_SERIALS['X'])

    def test_changed_source_requires_rebuilding(self):
        source=self.copied_artifacts()
        (source/'motion_core.h').write_text('// changed')
        with self.assertRaisesRegex(SystemExit,'source changed'):
            upload.prepared_target('Y',upload.MOTOR_SERIALS['Y'])

    def test_manifest_cannot_swap_the_known_boards(self):
        source=self.copied_artifacts()
        path=source/'hex/manifest.json';manifest=json.loads(path.read_text())
        manifest['axes']['X']['usbSerial']=upload.MOTOR_SERIALS['Y']
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SystemExit,'manifest'):
            upload.prepared_target('X',upload.MOTOR_SERIALS['X'])


if __name__=='__main__':unittest.main()

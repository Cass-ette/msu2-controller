"""Framing geometry, original retention and live/flash output consistency."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageTk

from image_viewport import ImageViewport,load_source
import test_features


def bands(size=(600,200)):
    source=Image.new('RGB',size,'green')
    draw=ImageDraw.Draw(source)
    draw.rectangle((0,0,size[0]//3-1,size[1]-1),fill='red')
    draw.rectangle((size[0]*2//3,0,size[0]-1,size[1]-1),fill='blue')
    return source


class GeometryTests(unittest.TestCase):
    def test_drag_reveals_edges_and_clamps_without_accidental_black_gaps(self):
        model=ImageViewport(bands(),(160,80))
        self.assertEqual(model.render().getpixel((80,40)),(0,128,0))
        model.pan(9999,0)
        self.assertEqual(model.render().getpixel((30,40)),(255,0,0))
        self.assertEqual(model.center,[200,100])
        model.pan(-9999,0)
        self.assertEqual(model.render().getpixel((130,40)),(0,0,255))
        self.assertEqual(model.center,[400,100])

    def test_zoom_anchor_retains_the_point_under_pointer(self):
        model=ImageViewport(bands())
        model.zoom_to(2)
        anchor=(50,30)
        before=[model.center[i]+(anchor[i]-model.size[i]/2)/model.scale for i in range(2)]
        model.zoom_to(3,anchor)
        after=[model.center[i]+(anchor[i]-model.size[i]/2)/model.scale for i in range(2)]
        self.assertEqual(before,after)

    def test_contain_shows_entire_vertical_image_and_rotation_fills_landscape(self):
        source=Image.new('RGB',(80,160),'red')
        model=ImageViewport(source)
        model.reset(contain=True)
        frame=model.render()
        self.assertEqual(frame.getpixel((0,40)),(16,24,36))
        self.assertEqual(frame.getpixel((80,40)),(255,0,0))
        model.rotate(True)
        self.assertEqual(model.source.size,(160,80))
        self.assertEqual(model.zoom,1)
        self.assertEqual(model.render().getpixel((0,40)),(255,0,0))

    def test_four_rotations_and_repeated_zoom_never_resample_original(self):
        source=bands((160,80))
        model=ImageViewport(source)
        initial=model.render().tobytes()
        for _ in range(4):
            model.rotate()
            model.zoom_to(5)
            model.pan(10,0)
        model.reset()
        self.assertEqual(model.original.tobytes(),source.tobytes())
        self.assertEqual(model.render().tobytes(),initial)

    def test_left_and_right_quarter_turns_are_opposites(self):
        source=bands((80,160))
        model=ImageViewport(source)
        model.rotate(False)
        self.assertEqual(model.source.tobytes(),source.transpose(Image.Transpose.ROTATE_90).tobytes())
        model.rotate(True)
        self.assertEqual(model.source.tobytes(),source.tobytes())

    def test_exif_orientation_and_transparency_are_applied_before_crop(self):
        source=Image.new('RGBA',(80,160),(255,0,0,0))
        source.getexif()[274]=6
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'oriented.png'
            source.save(path,exif=source.getexif())
            image=load_source(path,'#123456')
        self.assertEqual(image.size,(160,80))
        self.assertEqual(image.getpixel((40,40)),(18,52,86))


class EditorGuiTests(unittest.TestCase):
    setUp=test_features.GuiTests.setUp
    tearDown=test_features.GuiTests.tearDown
    complete=test_features.GuiTests.complete

    def test_cancel_keeps_existing_framing_and_main_image(self):
        from image_editor import ImageEditor
        old=ImageViewport(bands())
        before=old.render().tobytes()
        applied=[]
        editor=ImageEditor(self.root,old.original,(160,80),lambda *args:applied.append(args),model=old)
        editor.model.pan(50,0)
        editor.rotate(True)
        editor.close()
        self.assertEqual(applied,[])
        self.assertEqual(old.render().tobytes(),before)
        self.assertEqual(old.quarters,0)

    def test_main_apply_drag_rotation_then_live_and_burn_use_same_crop(self):
        import app
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'wide.png'
            bands().save(path)
            self.ui.edit_picture(str(path))
            editor=self.ui.image_editor
            editor.rotate(False)
            editor.model.zoom_to(2)
            editor.press(SimpleNamespace(x=100,y=100))
            editor.drag(SimpleNamespace(x=170,y=100))
            expected=editor.model.render()
            editor.apply()
            self.assertIsNone(self.ui.image_editor)
            self.assertEqual(self.ui.mode.get(),'图片')
            self.assertEqual(self.ui.image().tobytes(),expected.tobytes())
            self.ui.upside_down.set(True)
            self.ui.send()
            self.complete()
            oriented=expected.transpose(Image.Transpose.ROTATE_180)
            self.assertEqual(self.display.frames[-1].tobytes(),oriented.tobytes())
            with patch.object(app.messagebox,'askyesno',return_value=True):
                self.ui.burn_offline()
                self.complete()
            self.assertEqual(self.display.burns[-1].tobytes(),oriented.tobytes())
            preview=ImageTk.getimage(self.ui.photo).convert('RGB')
            self.assertEqual(preview.tobytes(),expected.resize(preview.size,Image.Resampling.NEAREST).tobytes())
            self.ui.adjust_image()
            self.assertEqual(self.ui.image_editor.model.original.size,(600,200))
            self.ui.image_editor.close()

    def test_clock_background_uses_applied_crop_and_can_be_edited_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'portrait.png'
            bands((80,160)).save(path)
            self.ui.edit_picture(str(path),clock=True)
            self.ui.image_editor.rotate(True)
            expected=self.ui.image_editor.model.render()
            self.ui.image_editor.apply()
            self.assertEqual(self.ui.clock_background.tobytes(),expected.tobytes())
            self.ui.adjust_clock_background()
            self.assertEqual(self.ui.image_editor.model.quarters,1)
            self.ui.image_editor.close()

    def test_asset_crop_keeps_original_backup_and_parent_grab(self):
        from assets_ui import AssetEditor
        from assets import AssetBackup
        editor=AssetEditor(self.ui)
        self.ui.asset_editor=editor
        original=AssetBackup('clock_background',b'\0'*25600)
        editor.backup=original
        editor.set_image(bands((160,80)))
        editor.adjust_image()
        editor.image_editor.model.zoom_to(2)
        editor.image_editor.model.pan(20,0)
        expected=editor.image_editor.model.render()
        editor.image_editor.apply()
        self.assertEqual(editor.image.tobytes(),expected.tobytes())
        self.assertIs(editor.backup,original)
        self.assertEqual(editor.window.grab_current(),editor.window)
        editor.use_clock_background()
        self.assertEqual(self.ui.clock_background.tobytes(),expected.tobytes())
        editor.close()


if __name__=='__main__':
    unittest.main(verbosity=2)

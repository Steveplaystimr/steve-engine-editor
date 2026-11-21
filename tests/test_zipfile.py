import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from engine_editor.editor import SteveEngine


class ZipCompressionTests(unittest.TestCase):
    def test_selected_files_are_compressed_into_zip_archive(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_file = root / "first.txt"
            second_file = root / "second.dat"
            archive_path = root / "project.zip"
            first_file.write_text("first contents", encoding="utf-8")
            second_file.write_bytes(b"second contents")

            editor = SteveEngine.__new__(SteveEngine)
            editor.root = Mock()
            editor.status = Mock()
            with (
                patch(
                    "engine_editor.editor.filedialog.askopenfilenames",
                    return_value=(str(first_file), str(second_file)),
                ),
                patch(
                    "engine_editor.editor.filedialog.asksaveasfilename",
                    return_value=str(archive_path),
                ),
                patch("engine_editor.editor.messagebox.showinfo") as showinfo,
            ):
                editor.open_zipfile()

            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    archive.namelist(), ["first.txt", "second.dat"],
                )
                self.assertEqual(
                    archive.read("first.txt"), b"first contents",
                )
                self.assertEqual(
                    archive.read("second.dat"), b"second contents",
                )
            editor.status.set.assert_called_once_with(
                "Created ZIP archive: project.zip",
            )
            showinfo.assert_called_once()

    def test_canceling_file_selection_does_not_create_archive(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.root = Mock()
        with patch(
            "engine_editor.editor.filedialog.askopenfilenames",
            return_value=(),
        ), patch(
            "engine_editor.editor.filedialog.asksaveasfilename",
        ) as save_dialog:
            editor.open_zipfile()

        save_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()

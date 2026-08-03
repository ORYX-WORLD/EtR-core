import subprocess
import unittest

from src.deploy.raspi.etr_sd_factory_diagnostics import explain_creation_error


class FactoryError(RuntimeError):
    pass


class SdFactoryDiagnosticTests(unittest.TestCase):
    def test_preserves_controlled_factory_error(self):
        self.assertEqual(
            explain_creation_error(FactoryError("Carte trop petite"), "/dev/sda", kernel_text=""),
            "Carte trop petite",
        )

    def test_detects_kernel_medium_error_and_sector(self):
        error = subprocess.CalledProcessError(
            1,
            ["/usr/sbin/mkfs.ext4", "-F", "-L", "rootfs", "/dev/sda2"],
        )
        message = explain_creation_error(
            error,
            "/dev/sda",
            kernel_text="critical medium error, dev sda, sector 22290432 op 0x0:(READ)",
        )
        self.assertIn("microSD est défectueuse", message)
        self.assertIn("22290432", message)
        self.assertIn("Remplacez cette carte", message)

    def test_detects_read_only_media(self):
        error = subprocess.CalledProcessError(
            1,
            ["/usr/sbin/mkfs.ext4", "/dev/sda2"],
            stderr="Read-only file system",
        )
        message = explain_creation_error(error, "/dev/sda", kernel_text="")
        self.assertIn("lecture seule", message)

    def test_detects_automatic_mount(self):
        error = subprocess.CalledProcessError(
            1,
            ["/usr/sbin/mkfs.ext4", "/dev/sda2"],
            stderr="/dev/sda2 is mounted; will not make a filesystem here!",
        )
        message = explain_creation_error(error, "/dev/sda", kernel_text="")
        self.assertIn("montée automatiquement", message)

    def test_generic_error_keeps_stderr_without_python_repr_only(self):
        error = subprocess.CalledProcessError(
            1,
            ["/usr/sbin/mkfs.ext4", "/dev/sda2"],
            stderr="unexpected formatter failure",
        )
        message = explain_creation_error(error, "/dev/sda", kernel_text="")
        self.assertIn("unexpected formatter failure", message)
        self.assertIn("Détail technique", message)


if __name__ == "__main__":
    unittest.main()

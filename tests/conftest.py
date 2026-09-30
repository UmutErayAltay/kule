"""Platform taşınabilirliği için ortak test fixtür'leri.

Testler POSIX `~` beklentisiyle yazıldı: `HOME=/fake` iken `~/x` = `/fake/x`
olmalı. Linux'un `posixpath.expanduser`'ı `HOME`'a bakar; Windows'un
`ntpath.expanduser`'ı ise `USERPROFILE`'a (ve `Path.home()` da onu kullanır),
yani aynı testler orada `C:\\kullanici\\x` beklentisiyle çalışır ve
`assert`'ler kırılır — uygulama mantığı doğru olsa bile.

`os.path` Windows'ta `ntpath` MODÜLÜNÜN kendisidir, `Path.home()` ve
`Path().expanduser()` da onu çağırır. Dolayısıyla `ntpath.expanduser`'ı
tek noktadan yamamak üçünü birden düzeltir.

ponytail: bu yamaların hepsi testlerin YAZILDIĞI sözleşmeyi Windows'ta da
geçerli kılar; uygulama kodunun hiçbir satırı değişmez.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ntpath  # noqa: F401  -- Windows'ta `os.path` bununla aynı modül nesnesi
import pytest

_ORIGINAL_EXPANDUSER = ntpath.expanduser


@pytest.fixture(autouse=True)
def _home_da_home(monkeypatch):
    """`HOME` tanımlıysa `~` onu gösterir — Windows'ta da, Linux'taki gibi.

    Test `HOME`'u geçici bir dizine yönlendirip `~/...` yazan değerlerin o
    dizine açılmasını bekler. Fixtür testten ÖNCE çalışsa `HOME`'u göremezdi;
    bu yüzden yamalanan fonksiyon `HOME`'u ÇAĞRI ANINDA okur.
    """
    if os.name != "nt":  # posixpath zaten HOME'u doğru okuyor
        return

    def expanduser(path):
        home = os.environ.get("HOME")
        if home and (path == "~" or path[1:2] in ("/", "\\")):
            if path == "~":
                return os.path.normpath(home)
            # join+normpath, düz birleştirme değil: `home + "/x"` karışık
            # ayraç üretir (`C:\yol\..\home/x`), testler `str(Path(..) / "x")`
            # ile karakter karakter karşılaştırıyor.
            return os.path.normpath(os.path.join(home, path[2:]))
        return _ORIGINAL_EXPANDUSER(path)

    monkeypatch.setattr(ntpath, "expanduser", expanduser)


def symlinks_available() -> bool:
    """Bu makinede sembolik bağ oluşturulabiliyor mu?

    Windows'ta `Path.symlink_to` `SeCreateSymbolicLinkPrivilege` ister (yoksa
    WinError 1314) — geliştirici modu ya da yönetici gerekir. Sembolik bağı
    atlayan kod yolları o olmadan test edilemez, o yüzden testler ATLAR.
    """
    if os.name != "nt":
        return True
    with tempfile.TemporaryDirectory() as tmp:
        try:
            Path(tmp, "bag").symlink_to(tmp)
        except (OSError, NotImplementedError, AttributeError):
            return False
    return True


def disk_root() -> str:
    """`maintenance_status.FALLBACK_DISK_PATH`'in bu platformdaki GERÇEK yolu.

    Testte `"/"` yazmak Windows'ta `str(Path("/")) == "\\"` olduğu için sahte
    `shutil.disk_usage` ile eşleşmez ve "erişilemedi" hatası üretir. Testler
    uygulamanın gerçekten çağırdığı yol anahtarını kullanmalı.
    """
    return str(Path("/"))


#: Sembolik bağ gerektiren testlerin ortak atlama nedeni.
symlink_skip = pytest.mark.skipif(
    not symlinks_available(), reason="platformda sembolik bağ oluşturma izni yok"
)

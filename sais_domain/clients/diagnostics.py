"""
Bakanlık SAIS diagnostic tip kodları.

``SaisSimClient.send_diagnostic_with_type(type_no=...)`` çağrısında
kullanılan **DiagnosticTypeNo** → açıklama metni eşlemesi. Bakanlık
spec'inde tanımlı sabit liste; resmi tablo elde edildikçe burada
genişletilir. Varsayılan değerler legacy istasyon kodundan alınmıştır.
"""
from __future__ import annotations


# Bilinen tip kodları. Yeni kodlar Bakanlık dokümantasyonu ile doğrulanarak
# eklenmeli; bilinmeyenler için ``diagnostic_detail()`` generic fallback üretir.
DIAGNOSTIC_TYPES: dict[int, str] = {
    # 126 — numune talep akışı bildirimleri (start / error / complete)
    126: "Numune talep akışı bildirimi",
}


def diagnostic_detail(type_no: int, *, default: str = "") -> str:
    """Tip numarasına karşılık gelen açıklama metnini döndürür.

    Tip kayıtlı değilse ``default`` (verilmişse) ya da
    ``f"Diagnostic Type {type_no}"`` üretilir; çağıran kodun ``KeyError``
    yakalamasına gerek yoktur.
    """
    return DIAGNOSTIC_TYPES.get(type_no) or default or f"Diagnostic Type {type_no}"

import numpy as np
import math

def df_to_json_records(df):
    """
    DataFrame'i JSON uyumlu records listesine çevirir.
    NaN / inf -> None
    np.int64 / np.float64 -> native int/float
    """
    records = []
    for row in df.to_dict(orient="records"):
        clean_row = {}
        for k, v in row.items():
            if isinstance(v, (np.floating, float)):
                if v is None or math.isnan(v) or math.isinf(v):
                    clean_row[k] = None
                else:
                    clean_row[k] = float(v)
            elif isinstance(v, (np.integer, int)):
                clean_row[k] = int(v)
            else:
                clean_row[k] = v
        records.append(clean_row)
    return records
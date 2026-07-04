"""
生成示例工参数据(混合4G/5G, 含故意冲突便于演示)
"""
import pandas as pd
import random
import os

random.seed(42)

rows = []

# ===== 场景1: 北京CBD密集城区 (混合4G/5G) =====
# 3个站点, 每个3个扇区
base_lat = 39.915
base_lon = 116.404

# 站点1: CBD-A
site_id = 1
for sector in range(3):
    rows.append({
        "ECGI": f"460-00-10001-{sector+1}",
        "小区名称": f"BD_A_{sector+1}",
        "站点名称": "BD_A",
        "制式": "LTE",
        "频点": 1850,
        "经度": base_lon + random.uniform(-0.001, 0.001),
        "纬度": base_lat + random.uniform(-0.001, 0.001),
        "方位角": sector * 120,
        "覆盖半径": 500,
        "TAC": 12345,
        "PCI": 100,  # 故意全部同PCI
        "站点类型": "陆地",
    })

# 站点2: CBD-B (~1.5km)
site_id = 2
for sector in range(3):
    rows.append({
        "ECGI": f"460-00-10002-{sector+1}",
        "小区名称": f"BD_B_{sector+1}",
        "站点名称": "BD_B",
        "制式": "LTE",
        "频点": 1850,
        "经度": base_lon + 0.013 + random.uniform(-0.001, 0.001),
        "纬度": base_lat + 0.008 + random.uniform(-0.001, 0.001),
        "方位角": sector * 120,
        "覆盖半径": 600,
        "TAC": 12345,
        "PCI": 103,  # Mod3冲突
        "站点类型": "陆地",
    })

# 站点3: CBD-C (~3km, NR)
site_id = 3
for sector in range(3):
    rows.append({
        "ECGI": f"460-01-10003-{sector+1}",
        "小区名称": f"BD_C_{sector+1}",
        "站点名称": "BD_C",
        "制式": "NR",
        "频点": 643333,
        "经度": base_lon + 0.025 + random.uniform(-0.001, 0.001),
        "纬度": base_lat + 0.015 + random.uniform(-0.001, 0.001),
        "方位角": sector * 120,
        "覆盖半径": 800,
        "TAC": 22345,
        "PCI": 200,
        "站点类型": "陆地",
    })

# ===== 场景2: 近海超远覆盖(青岛海岸) =====
sea_lat = 36.067
sea_lon = 120.383

# 近海站点1
rows.append({
    "ECGI": "460-02-20001-1",
    "小区名称": "QD_OFFSHORE_1",
    "站点名称": "QD_OFFSHORE_1",
    "制式": "NR",
    "频点": 643333,
    "经度": sea_lon,
    "纬度": sea_lat,
    "方位角": 90,
    "覆盖半径": 30000,  # 30km超远
    "TAC": 32345,
    "PCI": 500,
    "站点类型": "近海",
})
rows.append({
    "ECGI": "460-02-20001-2",
    "小区名称": "QD_OFFSHORE_2",
    "站点名称": "QD_OFFSHORE_2",
    "制式": "NR",
    "频点": 643333,
    "经度": sea_lon,
    "纬度": sea_lat,
    "方位角": 270,
    "覆盖半径": 30000,
    "TAC": 32345,
    "PCI": 530,  # Mod30冲突
    "站点类型": "近海",
})

# 海上平台(40km外)
rows.append({
    "ECGI": "460-02-20002-1",
    "小区名称": "SEA_PLATFORM_1",
    "站点名称": "SEA_PLATFORM",
    "制式": "NR",
    "频点": 643333,
    "经度": sea_lon + 0.4,
    "纬度": sea_lat + 0.05,
    "方位角": 270,
    "覆盖半径": 20000,
    "TAC": 32345,
    "PCI": 500,
    "站点类型": "近海",
})

# ===== 场景3: 郊区小型站点 =====
sub_lat = 40.0
sub_lon = 116.6
for i in range(3):
    rows.append({
        "ECGI": f"460-00-30001-{i+1}",
        "小区名称": f"SUB_{i+1}",
        "站点名称": "SUB_1",
        "制式": "LTE",
        "频点": 3800,
        "经度": sub_lon,
        "纬度": sub_lat,
        "方位角": i * 120,
        "覆盖半径": 1500,
        "TAC": 42345,
        "PCI": 50 + i,
        "站点类型": "陆地",
    })

# ===== 故意制造异常行(便于演示清洗) =====
rows.append({
    "ECGI": "460-00-99999-1",
    "小区名称": "BAD_CELL",
    "站点名称": "BAD",
    "制式": "LTE",
    "频点": 1850,
    "经度": 200.0,  # 经度超范围
    "纬度": 39.9,
    "方位角": 0,
    "覆盖半径": 500,
    "TAC": 12345,
    "PCI": 999,
    "站点类型": "陆地",
})
rows.append({
    "ECGI": "460-00-99998-1",
    "小区名称": "BAD_PCI",
    "站点名称": "BAD",
    "制式": "LTE",
    "频点": 1850,
    "经度": 116.404,
    "纬度": 39.915,
    "方位角": 0,
    "覆盖半径": 500,
    "TAC": 12345,
    "PCI": 800,  # 超出LTE范围(>503)
    "站点类型": "陆地",
})

df = pd.DataFrame(rows)
os.makedirs("static", exist_ok=True)
output = "static/sample_cells.xlsx"
df.to_excel(output, index=False)
print(f"已生成 {output}, 共 {len(df)} 行")
print(f"  - 4G小区: {len(df[df['制式']=='LTE'])}")
print(f"  - 5G小区: {len(df[df['制式']=='NR'])}")
print(f"  - 故意异常: 2行(经度超范围 + PCI超范围)")
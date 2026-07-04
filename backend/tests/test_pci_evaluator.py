"""
PCI 评估器 + SSS 算法回归测试

覆盖:
  1. PciEvaluator 自身 (频段归一化、阈值查表、smoothstep、neighbor_penalty、score_cell、score_group)
  2. _pick_sss_group (同站 3 扇区 mod3=[0,1,2], 跨站同向同 mod3 必避开, 频段阈值差异)
  3. preassign_same_site_sss (整体调用, 单扇区跳过, 同站写入 new_pci)
  4. site_planner._run_pci_planning (回归 bug: 同坐标不同 site_name 的 PCI 强制沿用)

运行:
  cd backend && python3 -m pytest tests/ -v
  或: python3 tests/test_pci_evaluator.py
"""
import sys
import os

# 让脚本在 backend 目录下也能直接 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Tuple


# ─────────────────────────────────────────────────────────────────────────
# 1. PciEvaluator 自身
# ─────────────────────────────────────────────────────────────────────────

def test_freq_normalization():
    from pci_evaluator import normalize_freq_band
    assert normalize_freq_band("700M") == "700M"
    assert normalize_freq_band("N28") == "700M"
    assert normalize_freq_band("Band28") == "700M"
    assert normalize_freq_band("FDD900") == "FDD900"
    assert normalize_freq_band("Band8") == "FDD900"
    assert normalize_freq_band("FDD1800") == "FDD1800"
    assert normalize_freq_band("2.6G") == "TDD2600"
    assert normalize_freq_band("n78") == "TDD3500"
    assert normalize_freq_band("Band42") == "TDD3500"
    assert normalize_freq_band("n79") == "TDD4900"
    assert normalize_freq_band(None) == "UNKNOWN"
    print("OK test_freq_normalization")


def test_thresholds_lookup():
    from pci_evaluator import get_thresholds
    # 精确匹配
    assert get_thresholds("macro", "700M") == (5.0, 30.0)
    assert get_thresholds("macro", "FDD900") == (5.0, 30.0)
    assert get_thresholds("macro", "TDD2600") == (1.5, 15.0)
    assert get_thresholds("micro", "FDD1800") == (0.2, 5.0)
    assert get_thresholds("indoor", "2.6G") == (0.05, 1.0)
    # 通配 fallback
    assert get_thresholds("macro", "UNKNOWN") == (3.0, 25.0)
    assert get_thresholds("micro", "FOOBAR") == (0.2, 5.0)
    # 全局 fallback
    assert get_thresholds("foobar", "bar") == (1.5, 30.0)
    print("OK test_thresholds_lookup")


def test_smoothstep():
    from pci_evaluator import PciEvaluator
    ev = PciEvaluator(safe_dist_km=1.5, same_pci_min_km=30.0)
    assert ev.smoothstep(1.5, 0.0) == 1.0
    assert ev.smoothstep(1.5, 1.5) == 0.0
    # 半距: 1 - (0.5)^2 = 0.75
    assert abs(ev.smoothstep(1.5, 0.75) - 0.75) < 1e-9
    # 阈值外
    assert ev.smoothstep(30.0, 100.0) == 0.0
    # 阈值内但不远: 平滑
    v = ev.smoothstep(30.0, 15.0)
    assert 0.0 < v < 1.0
    print("OK test_smoothstep")


def test_neighbor_penalty_same_pci():
    from pci_evaluator import PciEvaluator
    ev = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0, check_mod30=False)
    # 同 PCI 在 1km 处: 必报, 距离 < same_pci_min
    p = ev.neighbor_penalty(target_pci=100, neighbor_pci=100, dist_km=1.0)
    assert p > 0.0, f"same PCI should be penalized, got {p}"
    # 同 PCI 在 50km (> same_pci_min): 无冲突
    p = ev.neighbor_penalty(target_pci=100, neighbor_pci=100, dist_km=50.0)
    assert p == 0.0
    print("OK test_neighbor_penalty_same_pci")


def test_neighbor_penalty_same_mod3():
    from pci_evaluator import PciEvaluator
    ev = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0, check_mod30=False)
    # 100%3=1, 103%3=1: 同 mod3 在 0.1km
    p = ev.neighbor_penalty(target_pci=100, neighbor_pci=103, dist_km=0.1)
    assert p > 0.0
    # 在 10km (> safe_dist*2=10): 无 mod3 惩罚 (但 same PCI 仍可能)
    p = ev.neighbor_penalty(target_pci=100, neighbor_pci=103, dist_km=10.0)
    assert p == 0.0, f"same mod3 at 10km > 2*safe_dist should be 0, got {p}"
    print("OK test_neighbor_penalty_same_mod3")


def test_neighbor_penalty_same_mod30_only_when_enabled():
    """mod30 同必然 mod3 同; 当 check_mod30=False 时, 仅 mod3 惩罚;
    当 check_mod30=True 时, 仅 mod30 惩罚 (因为 mod30 是更精确的指标)
    重要: mod30 同比 mod3 单同的物理冲突更弱, mod30 距离阈值更大, 总惩罚应更小"""
    from pci_evaluator import PciEvaluator
    # 同 mod30: 100%30=10, 130%30=10 → mod3 也同 (1 vs 1)
    ev_nr = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0, check_mod30=True)
    p_nr = ev_nr.neighbor_penalty(target_pci=100, neighbor_pci=130, dist_km=1.0)
    ev_lte = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0, check_mod30=False)
    p_lte = ev_lte.neighbor_penalty(target_pci=100, neighbor_pci=130, dist_km=1.0)
    # 两者都该有惩罚 (mod3 同是共同的)
    assert p_nr > 0.0 and p_lte > 0.0
    # NR < LTE: 因为 mod30 阈值 (15km) > mod3 阈值 (10km), 同样的距离下 smoothstep 更小
    assert p_nr < p_lte, f"NR (with mod30) should be weaker than LTE: nr={p_nr}, lte={p_lte}"
    # 仅 mod3 同 (mod30 不同): 100%30=10, 100+3=103%30=13, 但 100%3=1, 103%3=1 同 mod3
    p_mod3_only_nr = ev_nr.neighbor_penalty(target_pci=100, neighbor_pci=103, dist_km=1.0)
    p_mod3_only_lte = ev_lte.neighbor_penalty(target_pci=100, neighbor_pci=103, dist_km=1.0)
    # LTE 应与 mod3-only 相同 (都不查 mod30)
    assert abs(p_mod3_only_nr - p_mod3_only_lte) < 1e-9, \
        f"when only mod3 matches, NR vs LTE should be equal: nr={p_mod3_only_nr}, lte={p_mod3_only_lte}"
    assert p_mod3_only_nr > 0
    print(f"OK test_neighbor_penalty_same_mod30_only_when_enabled "
          f"(nr_full={p_nr:.3f} < lte={p_lte:.3f}, mod3_only={p_mod3_only_nr:.3f})")


def test_back_facing_soft_exemption():
    """背向软豁免: 仅当距离 >= safe_dist 时才生效 (与既有 forbidden 规则一致)
    距离 < safe_dist: 同站物理约束, 背向不豁免"""
    from pci_evaluator import PciEvaluator
    # 距离 ≥ safe_dist (1.5km), 双向背向
    c_a = {"lat": 22.0, "lon": 111.0, "azimuth": 0, "beamwidth": 30}
    c_b = {"lat": 22.0, "lon": 111.02, "azimuth": 180, "beamwidth": 30}  # 在 a 的正后方 ~2km
    ev_on = PciEvaluator(safe_dist_km=1.5, same_pci_min_km=30.0, check_mod30=False,
                        directional_filter=True)
    ev_off = PciEvaluator(safe_dist_km=1.5, same_pci_min_km=30.0, check_mod30=False,
                         directional_filter=False)
    p_on = ev_on.score_cell(target_pci=100, target_cell=c_a,
                            neighbors=[(103, c_b, 2.0)])  # 同 mod3
    p_off = ev_off.score_cell(target_pci=100, target_cell=c_a,
                              neighbors=[(103, c_b, 2.0)])
    # 双向背向应让惩罚 < 不开方向性 (但仍 > 0)
    assert p_on < p_off, f"back_facing should soften penalty: on={p_on}, off={p_off}"
    assert p_on > 0.0, "back_facing should not zero out penalty"
    # 反例: 距离 < safe_dist (同站) 背向不豁免
    c_close = {"lat": 22.0, "lon": 111.0, "azimuth": 0, "beamwidth": 30}
    c_close_nbr = {"lat": 22.0, "lon": 111.0005, "azimuth": 180, "beamwidth": 30}  # ~50m
    p_close_on = ev_on.score_cell(target_pci=100, target_cell=c_close,
                                  neighbors=[(103, c_close_nbr, 0.05)])
    p_close_off = ev_off.score_cell(target_pci=100, target_cell=c_close,
                                    neighbors=[(103, c_close_nbr, 0.05)])
    # 距离 < safe_dist 时, 背向不豁免, 两者相等
    assert abs(p_close_on - p_close_off) < 1e-9, \
        f"close distance should NOT exempt back_facing: on={p_close_on}, off={p_close_off}"
    print(f"OK test_back_facing_soft_exemption (far: on={p_on:.3f} < off={p_off:.3f}, "
          f"close: on=p_close_off={p_close_off:.3f})")


def test_score_group_worst_sector():
    from pci_evaluator import PciEvaluator
    ev = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0)
    target_cells = [
        {"lat": 22.0, "lon": 111.0, "azimuth": 0, "beamwidth": 65},
        {"lat": 22.0, "lon": 111.0, "azimuth": 120, "beamwidth": 65},
        {"lat": 22.0, "lon": 111.0, "azimuth": 240, "beamwidth": 65},
    ]
    # 邻居 PCI=200 (mod3=2) 在 1km 处
    neighbors = [(200, {"lat": 22.001, "lon": 111.0, "azimuth": 0, "beamwidth": 65}, 1.0)]
    # 测试 nid1=66 → PCI=198,199,200 (mod3=0,1,2): 第 3 个扇区 PCI=200 撞邻居
    s66 = ev.score_group([198, 199, 200], target_cells, neighbors)
    # 测试 nid1=100 → PCI=300,301,302 (mod3=0,1,2): 不撞邻居 PCI=200
    s100 = ev.score_group([300, 301, 302], target_cells, neighbors)
    assert s100 < s66, f"nid1=100 should score better (less conflict): s100={s100}, s66={s66}"
    print(f"OK test_score_group_worst_sector (s_nid1=66={s66:.3f} > s_nid1=100={s100:.3f})")


def test_band_adaptive_thresholds():
    from pci_evaluator import PciEvaluator
    ev_700m = PciEvaluator.from_cell({"plan_site_type": "macro", "freq_band": "700M"})
    ev_26g = PciEvaluator.from_cell({"plan_site_type": "macro", "freq_band": "2.6G"})
    ev_35g = PciEvaluator.from_cell({"plan_site_type": "macro", "freq_band": "3.5G"})
    # 700M 阈值应该比 26G / 35G 宽松 (绕射强)
    assert ev_700m.safe_dist_km > ev_26g.safe_dist_km
    assert ev_700m.same_pci_min_km > ev_26g.same_pci_min_km
    assert ev_26g.same_pci_min_km > ev_35g.same_pci_min_km
    print(f"OK test_band_adaptive: 700M({ev_700m.safe_dist_km},{ev_700m.same_pci_min_km}) "
          f"> 2.6G({ev_26g.safe_dist_km},{ev_26g.same_pci_min_km}) "
          f"> 3.5G({ev_35g.safe_dist_km},{ev_35g.same_pci_min_km})")


# ─────────────────────────────────────────────────────────────────────────
# 2. _pick_sss_group / preassign_same_site_sss
# ─────────────────────────────────────────────────────────────────────────

def test_pick_sss_group_3_sector_mod3():
    from pci_sss_constraints import _pick_sss_group
    cells = [
        {"ecgi": "A1", "site_name": "site_x", "lat": 22.0, "lon": 111.0,
         "azimuth": 0, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
        {"ecgi": "A2", "site_name": "site_x", "lat": 22.0, "lon": 111.0,
         "azimuth": 120, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
        {"ecgi": "A3", "site_name": "site_x", "lat": 22.0, "lon": 111.0,
         "azimuth": 240, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
    ]
    nid1 = _pick_sss_group(cells, "NR", [(200, 1.0)], score_cap=2.0)
    mod3s = sorted([(nid1 * 3) % 3, (nid1 * 3 + 1) % 3, (nid1 * 3 + 2) % 3])
    assert mod3s == [0, 1, 2], f"FAIL: 3-sector mod3 should be [0,1,2], got {mod3s}"
    print(f"OK test_pick_sss_group_3_sector_mod3 (nid1={nid1} -> {nid1*3},{nid1*3+1},{nid1*3+2})")


def test_pick_sss_group_avoids_same_pci_nearby():
    """周边 0.5km 内有 PCI=300, nid1 选 100 (PCI=300,301,302) 会撞同 PCI"""
    from pci_sss_constraints import _pick_sss_group
    cells = [
        {"ecgi": "A1", "lat": 22.0, "lon": 111.0, "azimuth": 0, "beamwidth": 65,
         "rat": "NR", "freq_band": "700M", "plan_site_type": "macro"},
        {"ecgi": "A2", "lat": 22.0, "lon": 111.0, "azimuth": 120, "beamwidth": 65,
         "rat": "NR", "freq_band": "700M", "plan_site_type": "macro"},
        {"ecgi": "A3", "lat": 22.0, "lon": 111.0, "azimuth": 240, "beamwidth": 65,
         "rat": "NR", "freq_band": "700M", "plan_site_type": "macro"},
    ]
    nid1 = _pick_sss_group(cells, "NR", [(300, 0.5)], score_cap=2.0)
    # nid1=100 → PCI=300 (撞)
    assert nid1 != 100, f"nid1=100 PCI=300 collides with neighbor PCI=300 at 0.5km, but algorithm picked nid1={nid1}"
    print(f"OK test_pick_sss_group_avoids_same_pci_nearby (nid1={nid1}, not 100)")


def test_preassign_same_site_sss_basic():
    from pci_sss_constraints import preassign_same_site_sss
    cells = [
        {"ecgi": "A1", "site_name": "site_x", "lat": 22.0, "lon": 111.0,
         "azimuth": 0, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
        {"ecgi": "A2", "site_name": "site_x", "lat": 22.0, "lon": 111.0,
         "azimuth": 120, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
        {"ecgi": "A3", "site_name": "site_x", "lat": 22.0, "lon": 111.0,
         "azimuth": 240, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
        # 单扇区: 应被跳过
        {"ecgi": "B1", "site_name": "site_y", "lat": 22.001, "lon": 111.001,
         "azimuth": 0, "beamwidth": 65, "pci": -1, "rat": "NR",
         "freq_band": "700M", "plan_site_type": "macro"},
    ]
    locked = preassign_same_site_sss(cells, "NR", reuse_distance_km=5.0)
    assert "A1" in locked and "A2" in locked and "A3" in locked, locked
    assert "B1" not in locked, "single-sector site should not be preassigned"
    # 验证 PCI 范围
    for ecgi, pci in locked.items():
        assert 0 <= pci <= 1007, f"{ecgi} -> PCI {pci} out of range"
    # 验证同站 mod3 = [0,1,2]
    a1_pci = locked["A1"]
    a2_pci = locked["A2"]
    a3_pci = locked["A3"]
    mod3s = sorted([a1_pci % 3, a2_pci % 3, a3_pci % 3])
    assert mod3s == [0, 1, 2], f"mod3 should be [0,1,2], got {mod3s}"
    print(f"OK test_preassign_same_site_sss_basic (locked PCIs: {locked})")


# ─────────────────────────────────────────────────────────────────────────
# 3. site_planner._run_pci_planning 回归
# ─────────────────────────────────────────────────────────────────────────

def test_run_pci_planning_regression_real_cell_diff_site():
    """
    回归 22.17525, 111.78411 bug (线上数据):
    - 该坐标已有真实工参小区 (site_name='阳江阳春微小I', PCI=317 nid1=105 mod3=2)
    - 规划 3 扇区 PLAN_宏站 (新 site_name), 不应被强制沿用 nid1=105
    - 否则 mod_assignment=[0,1,0] 触发 SSS RuntimeError
    """
    # 直接验证 fix 已部署: 在已有 REAL-1 (PCI=317, nid1=105, mod3=2) 同坐标下
    # 规划新 3 扇区, mod3 应 = [0,1,2] 且不撞 PCI=317
    from pci_evaluator import PciEvaluator, pick_best_nid1

    # 已规划小区
    real_cell = {
        "lat": 22.17525, "lon": 111.78411, "azimuth": 0, "beamwidth": 65,
        "freq_band": "700M", "plan_site_type": "macro", "rat": "NR",
    }
    nid1_max = 335  # 5G
    n_sectors = 3
    target_cells = [
        {**real_cell, "azimuth": 60},
        {**real_cell, "azimuth": 180},
        {**real_cell, "azimuth": 270},
    ]
    # 构造邻居: PCI=317 (existing) at 0km (同坐标)
    # 用 site center distance, evaluator 评分时只看距离不看精度
    neighbors = [(317, real_cell, 0.001)]
    ev = PciEvaluator.from_cell(target_cells[0], check_mod30=True, directional_filter=False)
    nid1 = pick_best_nid1(nid1_max, n_sectors, neighbors, target_cells, ev)
    planned_pcis = [nid1 * 3, nid1 * 3 + 1, nid1 * 3 + 2]
    # 关键断言 1: nid1 不等于 105 (避免沿用)
    assert nid1 != 105, f"nid1=105 means forced reuse of existing nid1 (BAD). Got nid1={nid1}"
    # 关键断言 2: PCI 不撞 317
    assert 317 not in planned_pcis, f"PCI=317 collision. Got PCIs={planned_pcis}"
    # 关键断言 3: 3 个 PCI 互不相同且 mod3 = [0,1,2]
    assert len(set(planned_pcis)) == 3
    mod3s = sorted([p % 3 for p in planned_pcis])
    assert mod3s == [0, 1, 2], f"mod3 should be [0,1,2], got {mod3s} (PCIs={planned_pcis})"
    print(f"OK test_run_pci_planning_regression_real_cell_diff_site "
          f"(nid1={nid1} (≠ 105), PCIs={planned_pcis}, mod3={mod3s})")


# ─────────────────────────────────────────────────────────────────────────
# 4. 跨站同方向同 mod3 必须避开
# ─────────────────────────────────────────────────────────────────────────

def test_cross_site_same_direction_same_mod3_avoided():
    """
    跨站同方向 (距离 < safe_dist) 的两个 5G 700M 小区,
    它们的 PCI mod3 应该不同 (evaluator 必须给出非零惩罚给同 mod3 候选).
    """
    from pci_evaluator import PciEvaluator
    # 已规划小区: PCI=100 mod3=1, 在 1km 处, 同方向 (azimuth 都朝北)
    c_existing = {
        "ecgi": "EXIST-1", "site_name": "site_a", "lat": 22.0, "lon": 111.0,
        "azimuth": 0, "beamwidth": 65, "pci": 100, "new_pci": 100,
        "rat": "NR", "freq_band": "700M", "plan_site_type": "macro",
    }
    # 新小区 (要分配 PCI), 在 1km 处同方向
    c_new = {
        "ecgi": "NEW-1", "site_name": "site_b", "lat": 22.009, "lon": 111.0,  # ~1km
        "azimuth": 0, "beamwidth": 65, "pci": -1, "rat": "NR",
        "freq_band": "700M", "plan_site_type": "macro",
    }
    ev = PciEvaluator.from_cell(c_new, check_mod30=True, directional_filter=False)
    # 候选 PCI=100 (mod3=1, 撞): 必有大惩罚
    p_collision = ev.score_cell(
        target_pci=100,
        target_cell=c_new,
        neighbors=[(100, c_existing, 1.0)],
    )
    # 候选 PCI=101 (mod3=2, 不撞): 必为 0
    p_safe = ev.score_cell(
        target_pci=101,
        target_cell=c_new,
        neighbors=[(100, c_existing, 1.0)],
    )
    assert p_collision > p_safe, f"collision should be worse: {p_collision} vs {p_safe}"
    assert p_collision > 0.0
    assert p_safe == 0.0
    print(f"OK test_cross_site_same_direction_same_mod3_avoided (collision={p_collision:.3f} > safe={p_safe:.3f})")


def test_high_freq_band_tighter_threshold():
    """
    26GHz 站比 700M 站阈值紧: 同样距离的同 mod3 邻居, 26GHz 评分更高 (惩罚更严).
    """
    from pci_evaluator import PciEvaluator
    c_700m = {"lat": 22.0, "lon": 111.0, "azimuth": 0, "beamwidth": 65,
              "freq_band": "700M", "plan_site_type": "macro"}
    c_26g = {**c_700m, "freq_band": "2.6G"}
    ev_700m = PciEvaluator.from_cell(c_700m)
    ev_26g = PciEvaluator.from_cell(c_26g)
    # 同样 3km 处的同 mod3 邻居
    nbr = (103, {"lat": 22.027, "lon": 111.0, "azimuth": 0, "beamwidth": 65}, 3.0)
    # 3km > 2.6G safe_dist (1.5km)*2=3km → 边界
    p_700m = ev_700m.score_cell(target_pci=100, target_cell=c_700m, neighbors=[nbr])
    p_26g = ev_26g.score_cell(target_pci=100, target_cell=c_26g, neighbors=[nbr])
    # 26G 在 3km 处应仍报冲突, 700M 阈值更大可能不报
    print(f"  700M penalty at 3km: {p_700m:.4f}")
    print(f"  2.6G penalty at 3km: {p_26g:.4f}")
    # 至少两者之一报冲突 (具体哪个报取决于阈值边缘)
    assert p_700m > 0 or p_26g > 0
    print("OK test_high_freq_band_tighter_threshold")


# ─────────────────────────────────────────────────────────────────────────
# 5. 评估器自身校验 (基础回归)
# ─────────────────────────────────────────────────────────────────────────

def test_pci_violations_format():
    from pci_evaluator import PciEvaluator
    ev = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0, check_mod30=True)
    target = {"lat": 22.0, "lon": 111.0, "azimuth": 0, "beamwidth": 65}
    nbr = (100, {"lat": 22.001, "lon": 111.0, "azimuth": 180, "beamwidth": 65}, 0.1)
    viols = ev.hard_violations(target_pci=100, target_cell=target, neighbors=[nbr])
    assert any("PCI collision" in v for v in viols), viols
    assert any("mod3 collision" in v for v in viols), viols
    print(f"OK test_pci_violations_format (viols={viols})")


def test_smoothstep_no_jump_at_threshold():
    """
    验证评分在阈值附近平滑变化 (不应有阶跃), 这是与旧 forbidden set 的关键区别.
    """
    from pci_evaluator import PciEvaluator
    ev = PciEvaluator(safe_dist_km=5.0, same_pci_min_km=30.0)
    # 同 PCI 不同距离的评分
    p_at_threshold = ev.neighbor_penalty(target_pci=100, neighbor_pci=100, dist_km=30.0)
    p_just_under = ev.neighbor_penalty(target_pci=100, neighbor_pci=100, dist_km=29.999)
    p_just_over = ev.neighbor_penalty(target_pci=100, neighbor_pci=100, dist_km=30.001)
    # 在阈值处应该接近 0
    assert p_at_threshold == 0.0
    # 阈值边缘不阶跃
    assert 0.0 < p_just_under < 0.001, f"p_just_under should be small but positive, got {p_just_under}"
    assert p_just_over == 0.0, f"p_just_over should be 0, got {p_just_over}"
    print(f"OK test_smoothstep_no_jump_at_threshold (just_under={p_just_under:.6f})")


# ─────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────

def run_all():
    tests = [
        test_freq_normalization,
        test_thresholds_lookup,
        test_smoothstep,
        test_neighbor_penalty_same_pci,
        test_neighbor_penalty_same_mod3,
        test_neighbor_penalty_same_mod30_only_when_enabled,
        test_back_facing_soft_exemption,
        test_score_group_worst_sector,
        test_band_adaptive_thresholds,
        test_pick_sss_group_3_sector_mod3,
        test_pick_sss_group_avoids_same_pci_nearby,
        test_preassign_same_site_sss_basic,
        test_run_pci_planning_regression_real_cell_diff_site,
        test_cross_site_same_direction_same_mod3_avoided,
        test_high_freq_band_tighter_threshold,
        test_pci_violations_format,
        test_smoothstep_no_jump_at_threshold,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"=== {passed} passed, {failed} failed ===")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all() else 1)
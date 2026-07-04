"""
后端包初始化,确保相对导入路径统一
"""
import os
import sys

# 将backend目录加入路径,使模块间可以import
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
"""版本号一致性测试。

项目版本号分布在多处，历史上多次漂移（.sh 脚本曾停在 0.6.0、package_release
脚本漏改 standalone bat）。这里用测试锁住"所有版本点必须一致"，防止发版时
静默产出旧版本号的产物。

版本点：
  - pyproject.toml    `version = "X.Y.Z"`          （单一真源）
  - about_page.py     `APP_VERSION = "X.Y.Z"`
  - build_nuitka.bat  `set PRODUCT_VERSION=X.Y.Z`
  - build_nuitka_onefile.bat  `set PRODUCT_VERSION=X.Y.Z`
  - build_nuitka_onefile.sh  运行时从 pyproject.toml 读取（.sh 不硬编码）

注意：tests 通常不 import src（被测单元大多有 Qt/硬件依赖），本测试只做
纯文件字符串解析，无 Qt / J-Link / 硬件依赖，CI 可跑。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r'version\s*=\s*"([^"]+)"')
APP_VERSION_RE = re.compile(r'APP_VERSION\s*=\s*"([^"]+)"')
PRODUCT_VERSION_RE = re.compile(r"set PRODUCT_VERSION=([0-9][^ \r\n]*)")
# build_nuitka_onefile.sh 从 pyproject.toml 读取版本（sed 表达式提取引号内版本号）。
# 只断言"存在从 pyproject 读取"，不展开 sed 细节（避免正则转义陷阱）。
SH_READS_VERSION_RE = re.compile(r"PRODUCT_VERSION\s*=\s*\"\$\(sed[^\n]*pyproject\.toml")


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_pyproject_and_about_page_match():
    """pyproject.toml 与 about_page.py 版本一致。"""
    pyproject = _read("pyproject.toml")
    about = _read("src/ui/about_page.py")
    m1 = VERSION_RE.search(pyproject)
    m2 = APP_VERSION_RE.search(about)
    assert m1 and m2, "未找到版本号"
    assert m1.group(1) == m2.group(1)


def test_bat_files_match_pyproject():
    """两个 build_nuitka*.bat 的 PRODUCT_VERSION 与 pyproject 一致。"""
    pyproject = _read("pyproject.toml")
    expected = VERSION_RE.search(pyproject).group(1)
    for bat in ("build_nuitka.bat", "build_nuitka_onefile.bat"):
        m = PRODUCT_VERSION_RE.search(_read(bat))
        assert m, f"{bat} 缺少 set PRODUCT_VERSION="
        assert m.group(1) == expected, (
            f"{bat} PRODUCT_VERSION={m.group(1)} != pyproject {expected}"
        )


def test_onefile_sh_reads_version_from_pyproject():
    """build_nuitka_onefile.sh 不再硬编码版本号，改为从 pyproject 读取。

    防回归：sh 曾停在 0.6.0（落后 4 个版本）。脚本内不应再出现裸数字版本。
    """
    sh = _read("build_nuitka_onefile.sh")
    assert SH_READS_VERSION_RE.search(sh), (
        "build_nuitka_onefile.sh 应通过 sed 从 pyproject.toml 读取 PRODUCT_VERSION，"
        "而不是硬编码版本号"
    )
    # 不应残留硬编码版本（如 PRODUCT_VERSION=0.x.x）
    assert not PRODUCT_VERSION_RE.search(sh), (
        "build_nuitka_onefile.sh 不应硬编码 PRODUCT_VERSION，应读 pyproject"
    )


def test_standalone_sh_does_not_hardcode_version():
    """build_nuitka.sh 不需要版本号（Linux ELF 无 PE 版本 flag）。

    若未来加版本引用，必须同样从 pyproject 读取，不能硬编码。
    """
    sh = _read("build_nuitka.sh")
    assert not PRODUCT_VERSION_RE.search(sh), (
        "build_nuitka.sh 不应硬编码 PRODUCT_VERSION"
    )

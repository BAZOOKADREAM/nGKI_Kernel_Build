#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Idempotent fixes for the SUSFS v2.3.0 patch on the alioth/sm8250 4.19 kernel
(projects-nexus/nexus_kernel_xiaomi_sm8250, a16-stable).

The generic susfs_patch_to_4.19.patch fails on three hunks of this tree:
  * fs/namespace.c  hunk #1  (include + SUS_MOUNT externs / CL_COPY_MNT_NS)
  * fs/namespace.c  hunk #6  (vfs_kern_mount() no longer calls alloc_vfsmnt(),
                              the 4.19.325 CAF tree allocates in vfs_create_mount())
  * fs/super.c      hunk #1  (include + SUS_MOUNT externs)

Run from the kernel source root.
"""
import io
import os
import re
import sys


def read(path):
    with io.open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def write(path, data):
    with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(data)


def insert_after(text, anchor, block, marker, path):
    if marker and marker in text:
        print("[=] %s: already present, skipped." % path)
        return text, False
    pos = text.find(anchor)
    if pos < 0:
        sys.exit("[-] %s: anchor not found: %r" % (path, anchor[:70]))
    pos += len(anchor)
    print("[+] %s: patched." % path)
    return text[:pos] + block + text[pos:], True


def replace_once(text, old, new, path):
    if old not in text:
        if new in text:
            print("[=] %s: replacement already applied, skipped." % path)
            return text, False
        sys.exit("[-] %s: replacement target not found." % path)
    print("[+] %s: patched." % path)
    return text.replace(old, new, 1), True


INCLUDE_BLOCK = (
    "#ifdef CONFIG_KSU_SUSFS\n"
    "#include <linux/susfs_def.h>\n"
    "#endif // #ifdef CONFIG_KSU_SUSFS\n"
)

EXTERN_BLOCK = (
    "#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
    "extern bool susfs_is_current_ksu_domain(void);\n"
    "extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;\n"
    "\n"
    "#define CL_COPY_MNT_NS BIT(25) /* used by copy_mnt_ns() */\n"
    "\n"
    "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
)

SUPER_EXTERN_BLOCK = (
    "#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
    "extern bool susfs_is_current_ksu_domain(void);\n"
    "extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;\n"
    "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
)


def fix_namespace():
    path = os.path.join("fs", "namespace.c")
    text = read(path)

    text, _ = insert_after(
        text,
        "#include <linux/sched/task.h>\n",
        INCLUDE_BLOCK,
        "susfs_def.h",
        path,
    )

    text, _ = insert_after(
        text,
        '#include "pnode.h"\n#include "internal.h"\n',
        "\n" + EXTERN_BLOCK,
        "CL_COPY_MNT_NS BIT(25)",
        path,
    )

    old = (
        "\tmnt = alloc_vfsmnt(fc->source ?: \"none\");\n"
        "\tif (!mnt)\n"
        "\t\treturn ERR_PTR(-ENOMEM);\n"
    )
    new = (
        "#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
        "\t// - We will just stop checking for ksu process if /sdcard/Android is accessible,\n"
        "\t//   for the sake of performance\n"
        "\tif (static_branch_unlikely(&susfs_is_sdcard_android_data_not_decrypted)) {\n"
        "\t\tif (susfs_is_current_ksu_domain()) {\n"
        "\t\t\tmnt = susfs_alloc_non_unshare_ksu_vfsmnt(fc->source ?: \"none\");\n"
        "\t\t\tgoto bypass_orig_flow;\n"
        "\t\t}\n"
        "\t}\n"
        "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
        "\n"
        "\tmnt = alloc_vfsmnt(fc->source ?: \"none\");\n"
        "\n"
        "#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
        "bypass_orig_flow:\n"
        "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n"
        "\tif (!mnt)\n"
        "\t\treturn ERR_PTR(-ENOMEM);\n"
    )
    text, _ = replace_once(text, old, new, path)

    write(path, text)


def fix_super():
    path = os.path.join("fs", "super.c")
    text = read(path)

    text, _ = insert_after(
        text,
        "#include <linux/user_namespace.h>\n",
        INCLUDE_BLOCK,
        "susfs_def.h",
        path,
    )

    text, _ = insert_after(
        text,
        '#include "internal.h"\n',
        "\n" + SUPER_EXTERN_BLOCK,
        "extern bool susfs_is_current_ksu_domain(void);",
        path,
    )

    write(path, text)


JUMP_LABEL_FILES = (
    "fs/namespace.c",
    "fs/proc/cmdline.c",
    "fs/proc_namespace.c",
    "fs/super.c",
    "kernel/sys.c",
    "security/selinux/avc.c",
)


def ensure_jump_label(path):
    """static_branch_*() is used by the SUSFS hunks; make sure the API header
    is pulled in directly instead of relying on transitive includes."""
    path = path.replace("/", os.sep)
    text = read(path)
    if "linux/jump_label.h" in text:
        print("[=] %s: jump_label.h already included, skipped." % path)
        return
    match = re.search(r"^#include [^\n]*\n", text, re.M)
    if not match:
        sys.exit("[-] %s: no #include line found." % path)
    pos = match.end()
    text = text[:pos] + "#include <linux/jump_label.h>\n" + text[pos:]
    write(path, text)
    print("[+] %s: added linux/jump_label.h." % path)


def main():
    if not os.path.isfile("Makefile") or not os.path.isdir("fs"):
        sys.exit("[-] Please run this script from the kernel source root.")
    fix_namespace()
    fix_super()
    for rel in JUMP_LABEL_FILES:
        if os.path.isfile(rel.replace("/", os.sep)):
            ensure_jump_label(rel)
    print("[+] SUSFS 4.19 compatibility fixes applied.")
    verify_hooks()


HOOK_CHECKS = (
    ("fs/exec.c", "ksu_handle_execveat"),
    ("fs/open.c", "ksu_handle_faccessat"),
    ("fs/read_write.c", "ksu_handle_sys_read"),
    ("fs/stat.c", "ksu_handle_stat"),
    ("kernel/reboot.c", "ksu_handle_sys_reboot"),
    ("drivers/input/input.c", "ksu_handle_input_handle_event"),
    ("kernel/sys.c", "ksu_handle_setresuid"),
)

INCOMPATIBLE_CHECKS = (
    ("fs/read_write.c", "ksu_vfs_read_hook"),
    ("fs/read_write.c", "ksu_init_rc_hook"),
    ("fs/stat.c", "ksu_init_rc_hook"),
    ("drivers/input/input.c", "ksu_input_hook"),
    ("fs/exec.c", "ksu_execveat_hook"),
)


def verify_hooks():
    """ReSukiSU refuses to build when a KernelSU inline hook is missing."""
    missing = []
    for rel, symbol in HOOK_CHECKS:
        path = rel.replace("/", os.sep)
        if not os.path.isfile(path) or symbol not in read(path):
            missing.append("%s (%s)" % (symbol, rel))
        else:
            print("[+] hook ok: %s in %s" % (symbol, rel))
    for rel, symbol in INCOMPATIBLE_CHECKS:
        path = rel.replace("/", os.sep)
        if os.path.isfile(path) and symbol in read(path):
            missing.append("incompatible hook %s in %s" % (symbol, rel))
    if missing:
        print("[-] KernelSU inline hook verification failed:")
        for item in missing:
            print("    - %s" % item)
        sys.exit(1)
    print("[+] All KernelSU inline hooks verified.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
inspect-nro.py -- decode an NRO header and run hbloader's own checks against it.

Written for one specific failure: hbloader aborting with 2347-0018, which is a
single line in nx-hbloader's main.c --

    rc = svcMapProcessCodeMemory(g_procHandle, (u64)map_addr, (u64)nrobuf, total_size);
    if (R_FAILED(rc))
        diagAbortWithResult(MAKERESULT(Module_HomebrewLoader, 18));

Everything that call depends on comes from the header this script prints:
`size`, `bss_size`, and the three segment offsets. If any of them is wrong,
`total_size` is wrong, and the map fails no matter how much memory is free --
which is why "the NRO is too big" is the wrong explanation when much larger
NROs load fine on the same console.

    python3 tools/inspect-nro.py insaniquarium_nx.nro
"""
import struct
import sys


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def human(n):
    return f"{n:,} bytes ({n / 1048576:.2f} MB)"


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    with open(path, "rb") as f:
        data = f.read()

    filesize = len(data)
    print(f"file: {path}")
    print(f"      {human(filesize)} on disk")
    print()

    if filesize < 0x80:
        print("  FAIL: too small to contain an NRO header")
        return 1

    # NroStart is 0x10 bytes; NroHeader follows it.
    magic = data[0x10:0x14]
    print("NroHeader")
    print(f"  magic          {magic!r}", "ok" if magic == b"NRO0" else "** hbloader aborts here (error 5) **")
    if magic != b"NRO0":
        print()
        print("  Not an NRO. If this is the .elf renamed, that is the bug --")
        print("  elf2nro has to run on it first.")
        return 1

    version = u32(data, 0x14)
    size = u32(data, 0x18)
    flags = u32(data, 0x1C)
    segs = [(u32(data, 0x20 + i * 8), u32(data, 0x24 + i * 8)) for i in range(3)]
    bss = u32(data, 0x38)

    print(f"  version        {version}")
    print(f"  size           {size:#x}  {human(size)}")
    print(f"  flags          {flags:#x}")
    print(f"  bss_size       {bss:#x}  {human(bss)}")
    print()

    names = [".text", ".rodata", ".data"]
    print("Segments")
    for i, (off, sz) in enumerate(segs):
        print(f"  {names[i]:<8} file_off {off:#010x}  size {sz:#010x}  ({sz / 1048576:.2f} MB)")
    print()

    ok = True

    # --- hbloader's own bounds check (its error 6) -------------------------
    print("hbloader checks")
    for i, (off, sz) in enumerate(segs):
        bad = off >= size or sz > size or (off + sz) > size
        print(f"  {names[i]:<8} within size    {'FAIL' if bad else 'ok'}")
        if bad:
            ok = False
    if not ok:
        print("     -> hbloader would abort with 2347-0006, not 0018")

    # --- what actually gets mapped ----------------------------------------
    total = (size + bss + 0xFFF) & ~0xFFF
    rw = (segs[2][1] + bss + 0xFFF) & ~0xFFF
    print(f"  total_size     {total:#x}  {human(total)}   <- the map request")
    print(f"  rw_size        {rw:#x}  {human(rw)}")
    print()

    # --- sanity that a bad header would trip ------------------------------
    print("Sanity")
    problems = []
    if size > filesize:
        problems.append(f"header size ({human(size)}) exceeds the file on disk "
                        f"({human(filesize)}) -- the file is truncated, or the "
                        f"header is wrong. hbloader reads `size` bytes into its "
                        f"heap, so this is a real problem.")
    if size == 0:
        problems.append("header size is zero")
    if total > 0x40000000:
        problems.append(f"total_size is {human(total)} -- absurd. "
                        f"virtmemFindCodeMemory cannot place that, so the map "
                        f"fails with 2347-0018 regardless of free memory.")
    if bss > 0x10000000:
        problems.append(f"bss_size is {human(bss)} -- implausible; suspect a bad "
                        f"elf2nro conversion.")
    for i, (off, sz) in enumerate(segs):
        if off & 0xFFF:
            problems.append(f"{names[i]} file_off {off:#x} is not page aligned")
    if segs[0][0] != 0:
        problems.append(f".text does not start at 0 (starts at {segs[0][0]:#x})")

    if problems:
        for p in problems:
            print(f"  PROBLEM: {p}")
        ok = False
    else:
        print("  header looks well formed; nothing here explains 2347-0018")

    print()
    if filesize > size:
        extra = filesize - size
        print(f"Asset section: {human(extra)} appended after the NRO "
              f"(icon + NACP + any romfs).")
        print("  Not part of the code mapping, so it cannot cause 2347-0018.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

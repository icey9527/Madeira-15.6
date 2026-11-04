#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>

/* See build/dxmt-ios/prepare-compat.py: MADEIRA_AIRCONV_VARIANT changes the AIR
 * shape so the device can bisect what Apple's Metal compiler refuses. */
namespace dxmt::airconv_probe {

inline int variant() {
  /* Read every time: the Swift self-test exports the value before calling
   * the in-process probe, and other airconv users may run earlier. */
  const char *s = std::getenv("MADEIRA_AIRCONV_VARIANT");
  return s ? std::atoi(s) : 0;
}

/* MADEIRA file-driven experiment channel.
 *
 * A variant value has to be chosen when this header is compiled, so every edit
 * costs a CI build.  A named FLAG says the same thing from a file instead:
 * MADEIRA_AIRCONV_EDIT points at a text file, and a flag is active when its
 * name appears anywhere in it.  One build then answers any number of edits --
 * change the file, restart the App.
 *
 * The app publishes the path before the in-process probe runs (Wine exports
 * madeira.cfg's env.* lines too late for that) and madeira.cfg sets the same
 * variable as env.MADEIRA_AIRCONV_EDIT for a game.  An absent or unreadable
 * file just means "no flags": every predicate below still answers exactly as
 * it did before, so this is inert unless asked for.
 *
 * Names, one per line if you like:
 *   textures-31  drop-metadata  index-from-zero
 *   strip-attributes  drop-convergent
 *   compile-options  position-name  generated-output  apple-vs
 */
inline bool edit_flag(const char *name) {
  static char buf[8192];
  static int state = 0;            /* 0 unread, 1 read, 2 unreadable */
  if (state == 0) {
    state = 2;
    const char *path = std::getenv("MADEIRA_AIRCONV_EDIT");
    if (path && *path) {
      if (std::FILE *f = std::fopen(path, "rb")) {
        size_t n = std::fread(buf, 1, sizeof(buf) - 1, f);
        std::fclose(f);
        buf[n] = 0;
        state = 1;
      }
    }
  }
  if (state != 1 || !name || !*name) return false;
  return std::strstr(buf, name) != nullptr;
}

/* 1 / 3: declare air.max_textures as 31 instead of 128. */
inline bool textures_31() { return variant() == 1 || variant() >= 3 || edit_flag("textures-31"); }

/* 2 / 3: leave the per-texture/sampler metadata member out of the argument
 * buffer and feed the shader the constants it would have read: array length 1
 * (clamp a 2D index to 0) and minLOD clamp 0.  TextureMetadata(1, 0.0f). */
inline bool drop_metadata() { return variant() == 2 || variant() == 3 || variant() >= 5 ||
                                   edit_flag("drop-metadata"); }

/* 4 / 5: number textures and UAVs from 0 instead of from 128 (SRV) / 512 (UAV).
 *
 * Those bases are DXMT's own and say nothing about the device: the argument
 * buffer slot a texture lands in is chosen by the encoder, and the
 * `air.location_index` airconv writes is what MSL would have taken from
 * [[id(n)]].  The handed MSL argument buffer -- the one shape measured to
 * compile on this device -- numbers its texture [[id(0)]], not [[id(128)]].
 * Only the location index changes; the argument buffer's own member numbering
 * (StructurePtrOffset) is a separate thing and stays put. */
inline bool index_from_zero() { return variant() >= 4 || edit_flag("index-from-zero"); }

/* 6: strip every function attribute whose bitcode code is above 68.
 *
 * 68 is `noundef`, the last entry in LLVM 11's ATTR_KIND_* table; Apple's
 * reader -- reading this device's own AIR -- rejects code 70
 * ("LLVM ERROR: Unknown attribute kind (70)") and that code is `mustprogress`,
 * which is what LLVM 12, 13 and 15 all call 70.  So the reader is older than
 * LLVM 12, and every attribute at or below 68 is still safe.
 *
 * airconv's own IR needs exactly one of those newer codes: `mustprogress`,
 * placed by hand in four spots (air_builder.cpp CreateTextureFence /
 * CreateAtomicFence / CreateSetMeshProperties, air_operations.cpp
 * call_unpack_impl -- that last one is the vertex-attribute path).  Chasing
 * the four writers one at a time is fragile, so this drops the whole class
 * after runOptimizationPasses() and before the module is serialised.
 *
 * NOT "the one place every compilation path passes through" (this said that,
 * and it was wrong): DXSOCompile in dxso_compile.cpp never called
 * runOptimizationPasses at all, so every D3D9/FFP module reached the writer
 * with its attributes intact.  That is why the same file now injects the call
 * into dxso_compile.cpp as well -- see the note on its variant_files entry.
 *
 * The cost is optimisation quality only: these are promises the optimiser may
 * use, not semantics the hardware needs.
 *
 * 7: the same, plus `convergent` (code 43).
 *
 * Worth trying because one probe run already showed the shape that ought to be
 * clean failing anyway: ps_cbuffer's shader_main carries no attribute group at
 * all, yet its pipeline is refused.  43 is in LLVM 11's table, so this is not
 * about the reader -- if 43 turns out to matter it is about what Metal does
 * with the promise, which is a different question. */
inline bool strip_attributes() { return variant() == 6 || variant() >= 7 || edit_flag("strip-attributes"); }
inline bool drop_convergent() { return variant() >= 7 || edit_flag("drop-convergent"); }

} // namespace dxmt::airconv_probe

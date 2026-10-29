import pathlib
import shutil
import sys

source_root = pathlib.Path(sys.argv[1])
output_root = pathlib.Path(sys.argv[2])
deployment = tuple(int(part) for part in sys.argv[3].split('.'))
sources = (
    'airconv/nt/air_builder.cpp',
    'airconv/dxbc_converter_gs.cpp',
    'airconv/dxbc_signature.cpp',
)
for relative in sources:
    output = output_root / relative
    if deployment >= (16, 3):
        output.unlink(missing_ok=True)
        continue
    source = (source_root / relative).read_text(encoding='utf-8')
    if '#include <format>' not in source or 'std::format(' not in source:
        raise SystemExit(f'DXMT format compatibility pattern changed: {relative}')
    output.parent.mkdir(parents=True, exist_ok=True)
    compatible = source.replace('#include <format>', '#include <fmt/format.h>')
    compatible = compatible.replace('std::format(', 'fmt::format(')
    if relative == 'airconv/nt/air_builder.cpp':
        fma_old = '  auto &Context = getContext();\n  auto Attrs = AttributeList::get(\n      Context, {{~0U, Attribute::get(Context, Attribute::AttrKind::ReadNone)},\n                {~0U, Attribute::get(Context, Attribute::AttrKind::NoUnwind)},\n                {~0U, Attribute::get(Context, Attribute::AttrKind::WillReturn)}}\n  );\n\n  SmallVector<Value *> Ops;\n  SmallVector<Type *> Tys;\n\n  Tys.push_back(X->getType());\n  Ops.push_back(X);\n  Tys.push_back(Y->getType());\n  Ops.push_back(Y);\n  Tys.push_back(Z->getType());\n  Ops.push_back(Z);\n\n  std::string FnName = "air.fma";\n  FnName += getTypeOverloadSuffix(X->getType());\n\n  auto Fn = getModule()->getOrInsertFunction(FnName, FunctionType::get(X->getType(), Tys, false), Attrs);\n\n  return builder.CreateCall(Fn, Ops);\n'
        fma_new = "  /* MADEIRA: iOS 15.6's AGX compiler ICEs on air.fma.*; the no-fma probe\n   * fixture, which removes one DXBC mad, passes every pipeline.  DXBC mad\n   * does not require fused rounding, so lower it to the separate mul/add\n   * the older compiler accepts.  Keep the type checks above. */\n  return builder.CreateFAdd(builder.CreateFMul(X, Y), Z);\n"
        if compatible.count(fma_old) != 1:
            raise SystemExit('DXMT air.fma pattern changed: air_builder.cpp')
        compatible = compatible.replace(fma_old, fma_new)
        constants_include_old = '#include "llvm/IR/DerivedTypes.h"\n'
        constants_include_new = '#include "llvm/IR/Constants.h"\n#include "llvm/IR/DerivedTypes.h"\n'
        if compatible.count(constants_include_old) != 1:
            raise SystemExit('DXMT air_builder.cpp include anchor changed')
        compatible = compatible.replace(constants_include_old, constants_include_new)
        fastop_old = ('  auto OperandType = Operand->getType();\n'
                      '\n'
                      '  std::string FnName = "air.";\n'
                      '  if (FastVariant)\n'
                      '    FnName += "fast_";\n'
                      '  FnName += FnNames[Op];\n')
        fastop_new = ('  auto OperandType = Operand->getType();\n'
                      '\n'
                      '  std::string FnName = "air.";\n'
                      '  /* MADEIRA: this device rejects air.fast_saturate,\n'
                      '   * air.fast_rint and the v4 fast_clamp overload; use\n'
                      '   * the scalar forms Apple\'s own iOS 15 metallib uses. */\n'
                      '  if (Op == saturate || Op == rint) {\n'
                      '    llvm::Type *ScalarTy = OperandType->getScalarType();\n'
                      '    auto CallScalar = [&](llvm::Value *V) -> llvm::Value * {\n'
                      '      if (Op == saturate) {\n'
                      '        auto Fn = getModule()->getOrInsertFunction(\n'
                      '            "air.fast_clamp" + getTypeOverloadSuffix(ScalarTy),\n'
                      '            llvm::FunctionType::get(ScalarTy, {ScalarTy, ScalarTy, ScalarTy}, false), Attrs);\n'
                      '        return builder.CreateCall(Fn, {V, llvm::ConstantFP::get(ScalarTy, 0.0),\n'
                      '                                        llvm::ConstantFP::get(ScalarTy, 1.0)});\n'
                      '      }\n'
                      '      auto Fn = getModule()->getOrInsertFunction(\n'
                      '          "air.rint" + getTypeOverloadSuffix(ScalarTy),\n'
                      '          llvm::FunctionType::get(ScalarTy, {ScalarTy}, false), Attrs);\n'
                      '      return builder.CreateCall(Fn, {V});\n'
                      '    };\n'
                      '    if (auto *VT = llvm::dyn_cast<llvm::FixedVectorType>(OperandType)) {\n'
                      '      llvm::Value *Result = llvm::UndefValue::get(OperandType);\n'
                      '      for (unsigned i = 0; i < VT->getNumElements(); ++i)\n'
                      '        Result = builder.CreateInsertElement(\n'
                      '            Result, CallScalar(builder.CreateExtractElement(Operand, i)), i);\n'
                      '      return Result;\n'
                      '    }\n'
                      '    return CallScalar(Operand);\n'
                      '  }\n'
                      '  if (FastVariant)\n'
                      '    FnName += "fast_";\n'
                      '  FnName += FnNames[Op];\n')
        if compatible.count(fastop_old) != 1:
            raise SystemExit('DXMT air_builder.cpp FPUnOp pattern changed')
        compatible = compatible.replace(fastop_old, fastop_new)
    output.write_text(compatible, encoding='utf-8')
    print(f'iOS {sys.argv[3]} format compatibility: {relative}')

relative = 'winemetal/unix/cache.c'
source = (source_root / relative).read_text(encoding='utf-8')
replacements = (
    ('extern void MTLSetShaderCachePath(NSString* path);',
     'extern void MTLSetShaderCachePath(NSString* path) __attribute__((weak_import));'),
    ('extern NSString* MTLGetShaderCachePath();',
     'extern NSString* MTLGetShaderCachePath() __attribute__((weak_import));'),
    ('_WMTSetMetalShaderCachePath(void *obj) {\n  struct unixcall_setmetalcachepath *params = obj;',
     '_WMTSetMetalShaderCachePath(void *obj) {\n  struct unixcall_setmetalcachepath *params = obj;\n'
     '  if (!MTLSetShaderCachePath || !MTLGetShaderCachePath) {\n'
     '    params->ret_success = 0;\n'
     '    return 0;\n'
     '  }'),
)
for original, replacement in replacements:
    if source.count(original) != 1:
        raise SystemExit(f'DXMT Metal cache compatibility pattern changed: {original}')
    source = source.replace(original, replacement)
output = output_root / relative
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(source, encoding='utf-8')
print(f'iOS {sys.argv[3]} optional Metal cache symbols: {relative}')


# iOS 15 (Madeira port): MTLCopyAllDevices exists on iOS only from 18.0 and is
# weak-imported, so below that it binds to NULL and an unconditional call jumps
# to address 0 (measured on the device: D3D11 device enumeration crashed inside
# this function with pc=0).  Below iOS 18 there is one device, returned as a +1
# array so the Copy rule the callers release by still holds.
#
# The @available() form is deliberate.  A plain NULL test is the idiom for a
# weak import, but Metal's header declares this function without weak_import
# (the SDK's .tbd marks the symbol weak, the declaration does not), so clang
# warns -Wpointer-bool-conversion, folds the test to true and deletes the
# fallback -- shipping the same jump to 0 from a build that looks clean.  With
# @available() the compiler must emit a runtime version check, and the fallback
# stays reachable (checked with clang -target arm64-apple-ios15.6: only this
# form keeps -initWithObjects:count: in the object, which is what
# build/dxmt-ios/build.sh and tools/ci/verify-startup.sh now require).
#
# Both edits to this one file share a buffer and it is written once, and the
# written file is read back and checked.  That is not belt-and-braces: the
# second rewrite below used to re-read the pristine source and write the file
# again, which silently dropped this guard -- and with it the whole fix -- from
# every build from the commit that added the gpuResourceID experiment until the
# bug was found by disassembling a shipped dylib on the device.
relative = 'winemetal/unix/winemetal_unix.c'
source = (source_root / relative).read_text(encoding='utf-8')

original = '  params->ret = (obj_handle_t)MTLCopyAllDevices();\n'
replacement = (
    '  /* iOS < 18: MTLCopyAllDevices is weak-imported and NULL; use the one\n'
    '   * device there is, as a +1 array (the Copy rule the callers release). */\n'
    '  if (@available(macOS 10.11, iOS 18.0, *)) {\n'
    '    params->ret = (obj_handle_t)MTLCopyAllDevices();\n'
    '  } else {\n'
    '    id<MTLDevice> device = MTLCreateSystemDefaultDevice();\n'
    '    params->ret = (obj_handle_t)(device ? [[NSArray alloc] initWithObjects:&device count:1]\n'
    '                                        : [[NSArray alloc] init]);\n'
    '    [device release];\n'
    '  }\n'
)
if source.count(original) != 1:
    raise SystemExit(f'DXMT MTLCopyAllDevices pattern changed: {original!r}')
source = source.replace(original, replacement)
# Receipts: both must be in the file this script leaves behind.  The first one
# is also what tools/ci/verify-startup.sh greps the built dylib for, and the
# second is the name of the helper the block below injects.
mtlcad_marker = 'if (@available(macOS 10.11, iOS 18.0, *)) {'
print(f'iOS {sys.argv[3]} optional MTLCopyAllDevices: {relative}')

# iOS 15 (Madeira port) EXPERIMENT: MTLResource.gpuResourceID is a Metal 3
# (iOS 16) API -- calling it on 15.6 raises NSInvalidArgumentException
# ("-[AGXG13GFamilySampler gpuResourceID]: unrecognized selector") and the
# process dies. DXMT encodes these ids into its argument buffers; below iOS 16
# there is no id to encode, so hand the encoder 0, the value DXMT itself uses
# when argument buffers are unavailable (see the support_argument_buffers
# ternary). What this build measures is whether the shaders airconv generates
# actually need a real id -- device creation and the MSAA query decide that.
helper_anchor = '#import <Metal/Metal.h>\n'
if source.count(helper_anchor) != 1:
    raise SystemExit('DXMT winemetal_unix.c: Metal import anchor changed')
# Madeira-15.6: encoding 0 made every textured D3D11 draw fault with
# MTLCommandBufferErrorDomain Code=9 "Invalid Resource" (The Shell, 2026-10-26),
# because DXMT writes these ids straight into its argument buffers.  Below iOS 16
# a texture may still answer gpuResourceID (only the sampler was seen to lack
# it); otherwise encode the resource once with a throwaway MTLArgumentEncoder
# and read back the 8-byte handle Tier 2 writes -- the path MoltenVK takes on
# iOS 15.  That layout is undocumented, so MADEIRA_AB_HANDLE=0 restores 0.
helper = helper_anchor + r'''#import <objc/message.h>
/* iOS 15: gpuResourceID is Metal 3 (iOS 16).  Madeira-15.6: ask the resource if
 * it answers anyway, else read the handle back from a one-off argument encoding.
 * MADEIRA_AB_HANDLE=0: encode 0 as before.  Logs [ios15-ab] for the first ones. */
static inline uint64_t madeira_gpu_resource_id(id resource) {
  if (@available(iOS 16.0, macOS 13.0, *))
    return resource ? (uint64_t)[resource gpuResourceID]._impl : 0;
  if (!resource) return 0;
  static int enabled = -1;
  static unsigned said;
  if (enabled < 0) {
    const char *e = getenv("MADEIRA_AB_HANDLE");  /* 0: encode 0 below iOS 16 (textured D3D11 draws fault) */
    enabled = !(e && e[0] == '0' && !e[1]);
  }
  if (!enabled) return 0;
  uint64_t direct = 0, encoded = 0;
  int responds = [resource respondsToSelector:@selector(gpuResourceID)];
  int sampler = [resource conformsToProtocol:@protocol(MTLSamplerState)];
  if (responds) direct = ((uint64_t (*)(id, SEL))objc_msgSend)(resource, @selector(gpuResourceID));
  if (!direct) {
    @autoreleasepool {
      id<MTLDevice> device = [resource device];
      MTLArgumentDescriptor *arg = [MTLArgumentDescriptor argumentDescriptor];
      arg.index = 0;
      arg.access = MTLArgumentAccessReadOnly;
      if (sampler) {
        arg.dataType = MTLDataTypeSampler;
      } else {
        arg.dataType = MTLDataTypeTexture;
        arg.textureType = [(id<MTLTexture>)resource textureType];
      }
      id<MTLArgumentEncoder> enc = device ? [device newArgumentEncoderWithArguments:@[arg]] : nil;
      if (enc) {
        NSUInteger length = enc.encodedLength < 8 ? 8 : enc.encodedLength;
        id<MTLBuffer> buf = [device newBufferWithLength:length options:MTLResourceStorageModeShared];
        if (buf) {
          [enc setArgumentBuffer:buf offset:0];
          if (sampler) [enc setSamplerState:(id<MTLSamplerState>)resource atIndex:0];
          else [enc setTexture:(id<MTLTexture>)resource atIndex:0];
          __builtin_memcpy(&encoded, [buf contents], 8);
          [buf release];
        }
        [enc release];
      }
    }
  }
  uint64_t handle = direct ? direct : encoded;
  if (__atomic_fetch_add(&said, 1, __ATOMIC_RELAXED) < 12)
    fprintf(stderr, "[ios15-ab] %s responds=%d direct=0x%llx encoded=0x%llx -> 0x%llx (MADEIRA_AB_HANDLE=0 disables)\n",
            sampler ? "sampler" : "texture", responds, (unsigned long long)direct,
            (unsigned long long)encoded, (unsigned long long)handle);
  return handle;
}
'''
source = source.replace(helper_anchor, helper)
replacements = (
    ('info->gpu_resource_id = info->support_argument_buffers ? [sampler gpuResourceID]._impl : 0;',
     'info->gpu_resource_id = info->support_argument_buffers ? madeira_gpu_resource_id(sampler) : 0;'),
    ('info->gpu_resource_id = [ret gpuResourceID]._impl;',
     'info->gpu_resource_id = madeira_gpu_resource_id(ret);'),
    ('params->gpu_resource_id = [ret gpuResourceID]._impl;',
     'params->gpu_resource_id = madeira_gpu_resource_id(ret);'),
    ('info->gpu_resource_id = t ? [t gpuResourceID]._impl : 0;',
     'info->gpu_resource_id = t ? madeira_gpu_resource_id(t) : 0;'),
)
total = 0
for original, replacement in replacements:
    count = source.count(original)
    if count == 0:
        raise SystemExit(f'DXMT gpuResourceID pattern missing: {original!r}')
    total += count
    source = source.replace(original, replacement)
for pattern in ('[sampler gpuResourceID]', '[ret gpuResourceID]', '[t gpuResourceID]'):
    if pattern in source:
        raise SystemExit(f'DXMT winemetal_unix.c still calls {pattern}')
gpu_marker = 'madeira_gpu_resource_id'

output = output_root / relative
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(source, encoding='utf-8')

# The file the build compiles is the only thing that matters, so check it.
written = output.read_text(encoding='utf-8')
for marker in (mtlcad_marker, gpu_marker):
    if marker not in written:
        raise SystemExit(f'{relative}: {marker!r} missing from the written file')
print(f'iOS {sys.argv[3]} gpuResourceID guarded in {relative} ({total} sites)')


# iOS 15 (Madeira port): airconv stamps everything it emits as macOS, and the
# Metal runtime refuses such a library outright --
#
#   Failed to create MTLLibrary: ... "This library format is not supported on
#   this platform (or was built with an old version of the tools)"
#
# which is where the first real Draw() died on the device (d3dchk3 stage 2: the
# clear+readback path before it worked, D3DCompile and both Create*Shader calls
# returned S_OK).  The stamps an iOS 15.6 device accepts were read off the
# embedded command library this project already ships for iOS: container
# platform 0x0001 (iOS), OS 0x82, OS version 15.0, and per-function version tags
# air 2.4 / Metal 2.4.  Emit those.  AIRCONV-ios-stamps
airconv_files = (
    ('airconv/airconv_context.cpp', (
        ('"air64-apple-macosx14.0.0"', '"air64-apple-ios15.0.0"'),
        ('#include "llvm/IR/Constants.h"\n',
         '#include "llvm/ADT/SmallVector.h"\n'
         '#include "llvm/IR/Constants.h"\n'
         '#include "llvm/IR/Instructions.h"\n'
         '#include "llvm/IR/Intrinsics.h"\n'),
        ('static std::atomic_flag llvm_overwrite = false;\n'
         '\n'
         'void\n'
         'runOptimizationPasses(llvm::Module &M) {',
         'static bool isUnsupportedIntrinsic(llvm::Function *F) {\n'
         '  if (!F) return false;\n'
         '  auto name = F->getName();\n'
         '  return name.startswith("llvm.umin.") || name.startswith("llvm.usub.sat.");\n'
         '}\n'
         'static void lowerUnsupportedIntrinsics(llvm::Module &M) {\n'
         '  unsigned lowered = 0;\n'
         '  for (llvm::Function &F : M) {\n'
         '    llvm::SmallVector<llvm::CallInst *, 4> calls;\n'
         '    for (llvm::BasicBlock &BB : F)\n'
         '      for (llvm::Instruction &I : BB)\n'
         '        if (auto *CI = llvm::dyn_cast<llvm::CallInst>(&I))\n'
         '          if (isUnsupportedIntrinsic(CI->getCalledFunction()))\n'
         '            calls.push_back(CI);\n'
         '    for (llvm::CallInst *CI : calls) {\n'
         '      llvm::IRBuilder<> B(CI);\n'
         '      auto *L = CI->getArgOperand(0);\n'
         '      auto *R = CI->getArgOperand(1);\n'
         '      llvm::Value *V;\n'
         '      if (CI->getCalledFunction()->getName().startswith("llvm.umin."))\n'
         '        V = B.CreateSelect(B.CreateICmpULT(L, R), L, R);\n'
         '      else\n'
         '        V = B.CreateSelect(B.CreateICmpUGT(L, R), B.CreateSub(L, R),\n'
         '                           llvm::Constant::getNullValue(L->getType()));\n'
         '      CI->replaceAllUsesWith(V);\n'
         '      CI->eraseFromParent();\n'
         '      ++lowered;\n'
         '    }\n'
         '  }\n'
         '  for (auto it = M.begin(); it != M.end();) {\n'
         '    llvm::Function *F = &*it++;\n'
         '    if (F->isDeclaration() && F->use_empty() && isUnsupportedIntrinsic(F))\n'
         '      F->eraseFromParent();\n'
         '  }\n'
         '  if (lowered)\n'
         '    llvm::errs() << "[ios15-probe] lowered " << lowered\n'
         '                 << " unsupported intrinsic call(s)\\n";\n'
         '}\n'
         '\n'
         'static std::atomic_flag llvm_overwrite = false;\n'
         '\n'
         'void\n'
         'runOptimizationPasses(llvm::Module &M) {'),
    ), ('air64-apple-ios15.0.0',)),
    ('airconv/dxbc_converter.cpp', (
        ('"air64-apple-macosx15.0.0"', '"air64-apple-ios15.0.0"'),
        ('createUnsignedInteger(2), createUnsignedInteger(7), createUnsignedInteger(0)',
         'createUnsignedInteger(2), createUnsignedInteger(4), createUnsignedInteger(0)'),
        ('createUnsignedInteger(2), createUnsignedInteger(6), createUnsignedInteger(0)',
         'createUnsignedInteger(2), createUnsignedInteger(4), createUnsignedInteger(0)'),
        ('createString("Metal"), createUnsignedInteger(3), createUnsignedInteger(2), createUnsignedInteger(0)',
         'createString("Metal"), createUnsignedInteger(2), createUnsignedInteger(4), createUnsignedInteger(0)'),
        ('createString("Metal"), createUnsignedInteger(3), createUnsignedInteger(1), createUnsignedInteger(0)',
         'createString("Metal"), createUnsignedInteger(2), createUnsignedInteger(4), createUnsignedInteger(0)'),
    ), ('air64-apple-ios15.0.0',)),
    ('airconv/air_signature.cpp', (
        ('#include "air_signature.hpp"\n',
         '#include "air_signature.hpp"\n#include "ios15_probe.hpp"\n'),
        # MADEIRA: Apple writes its own vertex position output as
        #   !{!"air.position", !"air.arg_type_name", !"float4", !"air.arg_name", !"position"}
        # and airconv inserts !"air.invariant" between the first two, in both
        # OutputPosition handlers below.  This device refuses an airconv VERTEX
        # module on its own -- airconv VS + Apple MSL PS is still
        # AGXMetal13_3 Code=3 -- so the flag "no-invariant" omits it and the
        # probe can say whether that is what the compiler objects to.
        ('              md.string("air.position")\n'
         '                  ->string("air.invariant")\n'
         '                  ->string("air.arg_type_name")\n',
         '              md.string("air.position");\n'
         '              if (!dxmt::airconv_probe::edit_flag("no-invariant"))\n'
         '                  md.string("air.invariant");\n'
         '              md.string("air.arg_type_name")\n'),
        ('          md.string("air.position")\n'
         '            ->string("air.invariant")\n'
         '            ->string("air.arg_type_name")\n',
         '          md.string("air.position");\n'
         '          if (!dxmt::airconv_probe::edit_flag("no-invariant"))\n'
         '            md.string("air.invariant");\n'
         '          md.string("air.arg_type_name")\n'),
        ('              if (!dxmt::airconv_probe::edit_flag("no-invariant"))\n',
         '              if (!dxmt::airconv_probe::edit_flag("no-invariant") &&\n'
         '                  !dxmt::airconv_probe::edit_flag("apple-vs"))\n'),
        ('          if (!dxmt::airconv_probe::edit_flag("no-invariant"))\n',
         '          if (!dxmt::airconv_probe::edit_flag("no-invariant") &&\n'
         '              !dxmt::airconv_probe::edit_flag("apple-vs"))\n'),
        ('              md.string("air.arg_type_name")\n'
         '                  ->string(get_name(position.type))\n'
         '                  ->string("air.arg_name")\n'
         '                  ->string("mtl_position");\n',
         '              md.string("air.arg_type_name")\n'
         '                  ->string(get_name(position.type))\n'
         '                  ->string("air.arg_name")\n'
         '                  ->string((dxmt::airconv_probe::edit_flag("apple-vs") ||\n'
         '                            dxmt::airconv_probe::edit_flag("position-name"))\n'
         '                               ? "position" : "mtl_position");\n'),
        ('          md.string("air.arg_type_name")\n'
         '            ->string(get_name(position.type))\n'
         '            ->string("air.arg_name")\n'
         '            ->string("mtl_position");\n',
         '          md.string("air.arg_type_name")\n'
         '            ->string(get_name(position.type))\n'
         '            ->string("air.arg_name")\n'
         '            ->string((dxmt::airconv_probe::edit_flag("apple-vs") ||\n'
         '                      dxmt::airconv_probe::edit_flag("position-name"))\n'
         '                         ? "position" : "mtl_position");\n'),
        ('          md.string("air.vertex_output")\n'
         '            ->string("user(" + vertex_out.user + ")")\n'
         '            ->string("air.arg_type_name")\n',
         '          md.string("air.vertex_output");\n'
         '          if ((dxmt::airconv_probe::edit_flag("apple-vs") ||\n'
         '               dxmt::airconv_probe::edit_flag("generated-output")) &&\n'
         '              vertex_out.user == "texcoord0" &&\n'
         '              get_name(vertex_out.type) == "float4") {\n'
         '            md.string("generated(9texcoord0Dv4_f)");\n'
         '          } else {\n'
         '            md.string("user(" + vertex_out.user + ")");\n'
         '          }\n'
         '          md.string("air.arg_type_name")\n'),
    ), ('edit_flag("no-invariant")', 'edit_flag("apple-vs")')),
    ('airconv/dxso_compile.cpp', (
        ('"air64-apple-macosx15.0.0"', '"air64-apple-ios15.0.0"'),
        ('{u32_md(2), u32_md(7), u32_md(0)}', '{u32_md(2), u32_md(4), u32_md(0)}'),
        # MADEIRA: DXSOCompile is the ONE module exit on the whole airconv
        # pipeline that never runs runOptimizationPasses -- dxso_compile.cpp
        # builds the module and hands it straight to MetallibWriter -- so the
        # attribute strip that this file injects at the end of
        # runOptimizationPasses can never reach it.  D3D9/FFP is therefore the
        # only path whose metallib still carries the attributes the device's AIR
        # reader refuses (code 70 = mustprogress), and of the four places airconv
        # writes MustProgress by hand, air_operations.cpp:31 is the one that
        # reaches D3D9 (through pull_vec4_from_addr, the manual vertex fetch).
        #
        # Running the same pipeline as dxbc_converter.cpp is what D3D11 has
        # always had.  Note the experiment this sets up: the optimiser now runs
        # in BOTH arms (variant 0 and 6), so a difference between them isolates
        # the strip.
        ('  llvm::raw_svector_ostream os(compiled->bytes);\n'
         '  dxmt::metallib::MetallibWriter writer;\n',
         '  llvm::raw_svector_ostream os(compiled->bytes);\n'
         '  /* MADEIRA: see prepare-compat.py -- DXSOCompile never ran the passes the\n'
         '   * D3D11 path runs, and the attribute strip lives at the end of them. */\n'
         '  dxmt::runOptimizationPasses(*module);\n'
         '  dxmt::metallib::MetallibWriter writer;\n'),
    ), ('air64-apple-ios15.0.0', 'dxmt::runOptimizationPasses(*module);')),
    ('airconv/ffp_compile.cpp', (
        ('"air64-apple-macosx15.0.0"', '"air64-apple-ios15.0.0"'),
        ('{u32_md(2), u32_md(7), u32_md(0)}', '{u32_md(2), u32_md(4), u32_md(0)}'),
        ('{MDString::get(context, "Metal"), u32_md(3), u32_md(2), u32_md(0)}',
         '{MDString::get(context, "Metal"), u32_md(2), u32_md(4), u32_md(0)}'),
    ), ('air64-apple-ios15.0.0',)),
    ('airconv/metallib_writer.hpp', (
        ('enum class Platform : uint16_t { MTLBPlatform_macOS = 0x8001 };',
         'enum class Platform : uint16_t { MTLBPlatform_macOS = 0x8001, MTLBPlatform_iOS = 0x0001 };'),
        ('enum class OS : uint8_t {\n  MTLBOS_macOS = 0x81,\n};',
         'enum class OS : uint8_t {\n  MTLBOS_macOS = 0x81,\n  MTLBOS_iOS = 0x82,\n};'),
    ), ('MTLBOS_iOS',)),
    ('airconv/metallib_writer.cpp', (
        # iOS 15 (Madeira port): DXMT already dumps the compiled module on the
        # DXSO path (dxso_compile.cpp, DXMT_AIRCONV_DUMP).  The D3D11 path -- the
        # one that dies inside Apple's compiler as soon as a shader reads a
        # texture -- had no such hook, so this one sits in MetallibWriter::Write,
        # the single point every stage's module passes through, and drops the
        # LLVM IR text next to an index line.  The IR is what the rejected shader
        # has to be diffed with, and it is written before the Metal call that
        # wedges, so the dump survives a hung run.  Off unless the variable is
        # set (the app sets it from an env.DXMT_AIRCONV_DUMP line in madeira.cfg).
        ('#include "metallib_writer.hpp"\n',
         '#include "metallib_writer.hpp"\n#include <atomic>\n#include <cstdio>\n'
         '#include <cstdlib>\n#include <string>\n#include <unistd.h>\n'),
        ('void MetallibWriter::Write(const llvm::Module &module, raw_ostream &OS) {\n',
         '/* see prepare-compat.py: env-gated dump of the module AGX is about to get */\n'
         'static void MadeiraDumpAirconvModule(const llvm::Module &module) {\n'
         '  const char *dir = std::getenv("DXMT_AIRCONV_DUMP");\n'
         '  if (!dir || !dir[0])\n'
         '    return;\n'
         '  static std::atomic<uint64_t> counter{0};\n'
         '  uint64_t n = counter.fetch_add(1, std::memory_order_relaxed);\n'
         '  char stem[64];\n'
         '  std::snprintf(stem, sizeof(stem), "airconv_%d_%llu", (int)getpid(),\n'
         '                (unsigned long long)n);\n'
         '  std::string base = std::string(dir) + "/" + stem;\n'
         '  std::string text;\n'
         '  raw_string_ostream stream(text);\n'
         '  module.print(stream, nullptr);\n'
         '  stream.flush();\n'
         '  if (FILE *fp = std::fopen((base + ".ll").c_str(), "wb")) {\n'
         '    std::fwrite(text.data(), 1, text.size(), fp);\n'
         '    std::fclose(fp);\n'
         '  }\n'
         '  std::string index;\n'
         '  for (const llvm::Function &function : module)\n'
         '    index += function.getName().str() + "\\n";\n'
         '  if (FILE *fp = std::fopen((std::string(dir) + "/index.txt").c_str(), "ab")) {\n'
         '    std::string line = std::string(stem) + " " + std::to_string(text.size()) + " bytes\\n" + index;\n'
         '    std::fwrite(line.data(), 1, line.size(), fp);\n'
         '    std::fclose(fp);\n'
         '  }\n'
         '}\n'
         '\n'
         'void MetallibWriter::Write(const llvm::Module &module, raw_ostream &OS) {\n'
         '  MadeiraDumpAirconvModule(module);\n'),
        ('  header.Platform = Platform::MTLBPlatform_macOS;',
         '  header.Platform = Platform::MTLBPlatform_iOS;'),
        ('  header.VersionMajor = 2;\n  header.VersionMinor = 7;',
         '  header.VersionMajor = 2;\n  header.VersionMinor = 6;'),
        ('  header.OS = OS::MTLBOS_macOS;',
         '  header.OS = OS::MTLBOS_iOS;'),
        ('  header.OSVersionMajor = 14;\n  header.OSVersionMinor = 4;',
         '  header.OSVersionMajor = 15;\n  header.OSVersionMinor = 0;'),
        # one per function type (vertex, fragment, object, mesh, kernel)
        ('          .airVersionMajor = 2,\n          .airVersionMinor = 6,\n          .languageVersionMajor = 3,\n          .languageVersionMinor = 1,',
         '          .airVersionMajor = 2,\n          .airVersionMinor = 4,\n          .languageVersionMajor = 2,\n          .languageVersionMinor = 4,',
         None),
    ), ('MTLBPlatform_iOS', 'header.Platform = Platform::MTLBPlatform_iOS;', 'MadeiraDumpAirconvModule', 'DXMT_AIRCONV_DUMP',
        'header.VersionMajor = 2;', 'header.VersionMinor = 6;',
        'header.OS = OS::MTLBOS_iOS;', 'header.OSVersionMajor = 15;',
        'header.OSVersionMinor = 0;')),
)

for relative, pairs, markers in airconv_files:
    src = (source_root / relative).read_text(encoding='utf-8')
    for entry in pairs:
        original, replacement = entry[0], entry[1]
        want = entry[2] if len(entry) > 2 else 1
        count = src.count(original)
        if want is None:
            if count < 1:
                raise SystemExit(f'{relative}: no match for {original!r}')
        elif count != want:
            raise SystemExit(f'{relative}: expected {want} match(es), found {count}: {original!r}')
        src = src.replace(original, replacement)
    out = output_root / relative
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding='utf-8')
    written = out.read_text(encoding='utf-8')
    for marker in markers:
        if marker not in written:
            raise SystemExit(f'{relative}: {marker!r} missing from the written file')
    if 'air64-apple-macosx' in written:
        raise SystemExit(f'{relative}: a macOS AIR target survives')
    print(f'iOS {sys.argv[3]} airconv stamps: {relative}')


# iOS 15.6: Apple's own metallib writes texture argument types with a space
# after the comma (texture2d<float, sample>); airconv emits the compact form,
# and this device's AGX compiler refuses every texture shader that carries it.
# The string lives in air_signature.hpp, which many translation units include,
# so patch the header in the source tree this build reads (the CI checkout is
# ephemeral; upstream is never written).
header_rel = 'airconv/air_signature.hpp'
header = (source_root / header_rel).read_text(encoding='utf-8')
compact = '"," + access +'
spaced = '", " + access +'
compact_count = header.count(compact)
if compact_count:
    (source_root / header_rel).write_text(header.replace(compact, spaced), encoding='utf-8')
    print(f'iOS {sys.argv[3]} texture type names: {header_rel} ({compact_count} sites)')
elif header.count(spaced) < 16:
    raise SystemExit(f'{header_rel}: the texture type spelling is neither compact nor patched')

# ---------------------------------------------------------------------------
# iOS 15.6: bisect the one thing the device still refuses.
#
# The D3D11 path dies inside Apple's Metal compiler -- AGXMetal13_3 raises
# "Compiler encountered an internal error" from newRenderPipelineStateWithDescriptor,
# i.e. while the compiler service reads the AIR module -- exactly when a fragment
# shader reads a texture.  Everything else works on this device: MSL argument
# buffers, direct [[texture(n)]] binding, and metadata-declared argument buffers
# all build pipelines (measured; Documents/metal-selftest-A-D on the iPad).
#
# So the difference is in the AIR *shape* airconv emits.  This section adds one
# switch, MADEIRA_AIRCONV_VARIANT (env, read from madeira.cfg), that changes that
# shape without a rebuild per variant:
#
#   0 (default) nothing changed -- repro
#   1           air.max_textures 128 -> 31   (128 is macOS's; an iOS device's
#               per-stage texture argument table is 31, and only a shader that
#               actually uses a texture has to make that claim real)
#   2           drop the per-texture/sampler 64-bit metadata member (the
#               `air.indirect_constant` group); the shader is fed constants
#   3           both
#
# The switch is a runtime env var, so one build answers all of them: set the
# variant in madeira.cfg, launch, read the dump and the PSO error.
probe_header = """#pragma once

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
"""
probe_out = output_root / 'airconv/ios15_probe.hpp'
probe_out.parent.mkdir(parents=True, exist_ok=True)
probe_out.write_text(probe_header, encoding='utf-8')
if 'MADEIRA_AIRCONV_VARIANT' not in probe_out.read_text(encoding='utf-8'):
    raise SystemExit('ios15_probe.hpp: the switch went missing from the file')
print(f'iOS {sys.argv[3]} airconv variant switch: airconv/ios15_probe.hpp')

CONST = ('llvm::ConstantInt::get(llvm::Type::getInt64Ty(ctx.builder.getContext()), '
         '1ull << 32)')
variant_files = (
    ('airconv/airconv_context.cpp', (
        ('#include "airconv_context.hpp"\n',
         '#include "airconv_context.hpp"\n#include "ios15_probe.hpp"\n'),
        ('  M.addModuleFlag(Module::ModFlagBehavior::Max, "air.max_textures", 128);',
         '  /* 128 is macOS\'s per-stage texture count; an iOS device is 31. */\n'
         '  M.addModuleFlag(\n'
         '    Module::ModFlagBehavior::Max, "air.max_textures",\n'
         '    dxmt::airconv_probe::textures_31() ? 31 : 128\n'
         '  );'),
        ('  M.addModuleFlag(Module::ModFlagBehavior::Max, "air.max_samplers", 16);\n',
         '  M.addModuleFlag(Module::ModFlagBehavior::Max, "air.max_samplers", 16);\n'
         '  if (dxmt::airconv_probe::edit_flag("apple-vs") ||\n'
         '      dxmt::airconv_probe::edit_flag("compile-options")) {\n'
         '    auto *options = M.getOrInsertNamedMetadata("air.compile_options");\n'
         '    options->addOperand(MDTuple::get(M.getContext(), {\n'
         '      MDString::get(M.getContext(), "air.compile.denorms_disable")\n'
         '    }));\n'
         '    options->addOperand(MDTuple::get(M.getContext(), {\n'
         '      MDString::get(M.getContext(), "air.compile.fast_math_enable")\n'
         '    }));\n'
         '    options->addOperand(MDTuple::get(M.getContext(), {\n'
         '      MDString::get(M.getContext(), "air.compile.framebuffer_fetch_enable")\n'
         '    }));\n'
         '  }\n'),
    ), ('dxmt::airconv_probe::textures_31()', 'edit_flag("apple-vs")')),
    ('airconv/dxbc_converter.cpp', (
        ('#include "dxbc_converter.hpp"\n',
         '#include "dxbc_converter.hpp"\n#include "ios15_probe.hpp"\n'),
        # Variant 4/5: renumber textures and UAVs from 0 (SRV and UAV alike, so
        # the two never collide); see index_from_zero() in the probe header.
        ('    auto attr_index = GetArgumentIndex(SM50BindingType::SRV, range_id);\n',
         '    auto attr_index = dxmt::airconv_probe::index_from_zero()\n'
         '                        ? range_id * 3\n'
         '                        : GetArgumentIndex(SM50BindingType::SRV, range_id);\n'),
        ('    auto attr_index = GetArgumentIndex(SM50BindingType::UAV, range_id);\n',
         '    auto attr_index = dxmt::airconv_probe::index_from_zero()\n'
         '                        ? range_id * 3 + 64\n'
         '                        : GetArgumentIndex(SM50BindingType::UAV, range_id);\n'),
        ('    sampler.arg_metadata_index = binding_table.DefineInteger64(\n'
         '      "meta_s" + std::to_string(range_id), attr_index + 2\n'
         '    );',
         '    /* Defined even when the member is dropped: the reader below captures\n'
         '     * it by value, and an uninitialised index would be UB. */\n'
         '    sampler.arg_metadata_index = 0;\n'
         '    if (!dxmt::airconv_probe::drop_metadata())\n'
         '      sampler.arg_metadata_index = binding_table.DefineInteger64(\n'
         '        "meta_s" + std::to_string(range_id), attr_index + 2\n'
         '      );'),
        ('      [=, index = sampler.arg_metadata_index](pvalue) {\n'
         '        // ignore index in SM 5.0\n'
         '        return get_item_in_argbuf_binding_table(binding_table_index, index);\n'
         '      }',
         '      [=, index = sampler.arg_metadata_index](pvalue) -> IRValue {\n'
         '        if (dxmt::airconv_probe::drop_metadata())\n'
         '          return make_irvalue([=](context ctx) -> pvalue { return ' + CONST + '; });\n'
         '        return get_item_in_argbuf_binding_table(binding_table_index, index);\n'
         '      }'),
        ('    srv.arg_metadata_index = binding_table.DefineInteger64(\n'
         '      "meta_t" + std::to_string(range_id), attr_index + 1\n'
         '    );',
         '    srv.arg_metadata_index = 0;  /* see the sampler above */\n'
         '    if (!dxmt::airconv_probe::drop_metadata())\n'
         '      srv.arg_metadata_index = binding_table.DefineInteger64(\n'
         '        "meta_t" + std::to_string(range_id), attr_index + 1\n'
         '      );'),
        ('        [=, index = srv.arg_metadata_index](pvalue) {\n'
         '          // ignore index in SM 5.0\n'
         '          return get_item_in_argbuf_binding_table(binding_table_index, index);\n'
         '        },\n'
         '        false',
         '        [=, index = srv.arg_metadata_index](pvalue) -> IRValue {\n'
         '          if (dxmt::airconv_probe::drop_metadata())\n'
         '            return make_irvalue([=](context ctx) -> pvalue { return ' + CONST + '; });\n'
         '          return get_item_in_argbuf_binding_table(binding_table_index, index);\n'
         '        },\n'
         '        false'),
    ), ('dxmt::airconv_probe::drop_metadata()',)),
    ('dxmt/dxmt_context.cpp', (
        ('#include "util_madeira_switch.hpp"\n',
         '#include "util_madeira_switch.hpp"\n#include "../airconv/ios15_probe.hpp"\n'),
        ('      encoded_buffer[arg.StructurePtrOffset + 2] = (uint64_t)std::bit_cast<uint32_t>(sampler->lod_bias);',
         '      if (!dxmt::airconv_probe::drop_metadata())\n'
         '        encoded_buffer[arg.StructurePtrOffset + 2] = (uint64_t)std::bit_cast<uint32_t>(sampler->lod_bias);'),
        ('          encoded_buffer[arg.StructurePtrOffset + 1] = TextureMetadata(srv.texture->arrayLength(viewIdChecked), min_lod);',
         '          if (!dxmt::airconv_probe::drop_metadata())\n'
         '            encoded_buffer[arg.StructurePtrOffset + 1] = TextureMetadata(srv.texture->arrayLength(viewIdChecked), min_lod);'),
    ), ('dxmt::airconv_probe::drop_metadata()',)),
    ('airconv/airconv_context.cpp', (
        ('#include "airconv_context.hpp"\n',
         '#include "airconv_context.hpp"\n#include "ios15_probe.hpp"\n'),
        # iOS 15.6 ALWAYS strips the attributes above code 68.  The device's AIR
        # reader is older than LLVM 12 and rejects mustprogress (code 70); every
        # module that carries a declaration with it is refused with the AGX ICE.
        # This is the one
        # place every compilation path goes through -- dxbc_converter.cpp's five
        # SM50Compile/SM50CompileGSPassthrough entries all call
        # runOptimizationPasses() and then hand the module to the metallib writer
        # -- so the whole class of newer attributes can be dropped here instead of
        # at each of the four places airconv writes MustProgress by hand.
        ('  // Optimize the IR!\n'
         '  MPM.run(M, MAM);\n'
         '}\n',
         '  // Optimize the IR!\n'
         '  MPM.run(M, MAM);\n'
         '\n'
         '  /* D3D9 vertex-fetch indexing reaches these through InstCombine; the\n'
         '   * iOS 15 AIR reader does not know either.  Lower after every pass. */\n'
         '  lowerUnsupportedIntrinsics(M);\n'
         '\n'
         '  /* The attributes the device\'s AIR reader refuses (see ios15_probe.hpp).\n'
         '   * This is the last pass over the IR: every entry point in\n'
         '   * dxbc_converter.cpp calls runOptimizationPasses() and then hands the\n'
         '   * module straight to the metallib writer, while linkShader() (which runs\n'
         '   * earlier, for MSAD/sample-pos/tessellation) only parses and links\n'
         '   * prebuilt bitcode and adds no attributes of its own. */\n'
         '  {\n'
         '    /* The reader is older than this airconv\'s LLVM, so even an\n'
         '     * attribute it nominally knows can be encoded in a way it\n'
         '     * refuses.  Attributes are optimisation hints; drop every\n'
         '     * enum attribute from functions and calls.  Type attributes\n'
         '     * (sret/byval/...) are left alone. */\n'
         '    llvm::AttributeMask mask;\n'
         '    for (unsigned code = 0; code < llvm::Attribute::EndAttrKinds; ++code) {\n'
         '      auto kind = static_cast<llvm::Attribute::AttrKind>(code);\n'
         '      if (llvm::Attribute::isEnumAttrKind(kind))\n'
         '        mask.addAttribute(kind);\n'
         '    }\n'
         '    unsigned fn_hits = 0, call_hits = 0;\n'
         '    for (llvm::Function &F : M) {\n'
         '      if (F.hasFnAttribute(llvm::Attribute::MustProgress))\n'
         '        ++fn_hits;\n'
         '      F.removeFnAttrs(mask);\n'
         '      for (unsigned i = 0; i < F.arg_size(); ++i)\n'
         '        F.removeParamAttrs(i, mask);\n'
         '      for (llvm::BasicBlock &BB : F)\n'
         '        for (llvm::Instruction &I : BB)\n'
         '          if (auto *CB = llvm::dyn_cast<llvm::CallBase>(&I)) {\n'
         '            if (CB->hasFnAttr(llvm::Attribute::MustProgress))\n'
         '              ++call_hits;\n'
         '            CB->removeFnAttrs(mask);\n'
         '            for (unsigned i = 0; i < CB->arg_size(); ++i)\n'
         '              CB->removeParamAttrs(i, mask);\n'
         '          }\n'
         '    }\n'
         '    llvm::errs() << "[ios15-probe] attribute masks removed from " << fn_hits\n'
         '                 << " function(s) and " << call_hits << " call(s)" << "\\n";\n'
         '  }\n'
         '}\n'),
    ), ('attribute masks removed from', 'lowerUnsupportedIntrinsics')),
)

for relative, pairs, markers in variant_files:
    # Read what the section above left behind when it wrote this file, not the
    # pristine source: several of these files already carry the iOS AIR stamps,
    # and re-reading source_root here would write them back to macOS -- the same
    # silent drop the note at the top of this script is about.
    out = output_root / relative
    src = (out if out.exists() else source_root / relative).read_text(encoding='utf-8')
    for entry in pairs:
        original, replacement = entry[0], entry[1]
        count = src.count(original)
        if count != 1:
            raise SystemExit(f'{relative}: expected 1 match, found {count}: {original[:70]!r}')
        src = src.replace(original, replacement)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding='utf-8')
    written = out.read_text(encoding='utf-8')
    for marker in markers:
        if marker not in written:
            raise SystemExit(f'{relative}: {marker!r} missing from the written file')
    print(f'iOS {sys.argv[3]} airconv variant switch: {relative}')

# ---------------------------------------------------------------------------
# iOS 15 native D3D9: SDL2/Ponscripter locks a streaming texture, writes it,
# then draws without ever calling UnlockRect.  The native frontend uploads the
# mirror only on Unlock, so the sampled texture stays blank.  Push a still-
# locked level's bytes on SetTexture without releasing the application's lock.
# The frontend sources include their headers with quoted paths.  Copy the whole
# d3d9 tree, not just the files this script edits: otherwise a pristine header
# reachable from the original source directory wins the compiler's quoted-
# include search and the modified class definitions never enter every TU.
d3d9_source = source_root / 'd3d9'
d3d9_output = output_root / 'd3d9'
for source in d3d9_source.rglob('*'):
    if not source.is_file():
        continue
    destination = output_root / source.relative_to(source_root)
    if destination.exists():
        continue
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

d3d9_files = (
    ('d3d9/d3d9_common_texture.hpp', (
        ('  virtual void\n  sweepManagedUpload() {}\n',
         '  virtual void\n  sweepManagedUpload() {}\n'
         '  // A texture still locked at draw time: SetTexture pushes its current\n'
         '  // bytes because the app may never call UnlockRect (SDL2/Ponscripter).\n'
         '  virtual void\n  flushLockedLevels() {}\n'),
    ), ('flushLockedLevels',)),
    ('d3d9/d3d9_surface.hpp', (
        ('  bool\n  lockedNoDirtyUpdate() const {\n    return m_locked_no_dirty_update;\n  }\n',
         '  bool\n  lockedNoDirtyUpdate() const {\n    return m_locked_no_dirty_update;\n  }\n'
         '  // Upload the still-mapped rect without clearing the lock state.\n'
         '  void\n  flushLockedUpload();\n'),
    ), ('flushLockedUpload',)),
    ('d3d9/d3d9_surface.cpp', (
        ('HRESULT STDMETHODCALLTYPE\nMTLD3D9Surface::GetDC(HDC *phdc) {\n',
         'void\nMTLD3D9Surface::flushLockedUpload() {\n'
         '  if (!m_locked || m_locked_readonly || m_buffer != nullptr || m_cpu_ptr == nullptr || m_texture == nullptr)\n'
         '    return;\n'
         '  if (!(m_desc.Pool == D3DPOOL_MANAGED || m_desc.Pool == D3DPOOL_DEFAULT) || IsNullFormat(m_desc.Format) ||\n'
         '      m_locked_w == 0 || m_locked_h == 0)\n'
         '    return;\n'
         '  static unsigned flush_logged = 0;\n'
         '  if (flush_logged++ < 8)\n'
         '    Logger::warn(str::format(\n'
         '        "[d9-locked-flush] level=", m_mip_level, " locked=", m_locked_w, "x", m_locked_h, " pool=", m_desc.Pool,\n'
         '        " fmt=", m_desc.Format, " buffer=", m_buffer != nullptr ? 1 : 0,\n'
         '        " cpu=", m_cpu_ptr != nullptr ? 1 : 0));\n'
         '  uint32_t x = m_locked_x, y = m_locked_y, w = m_locked_w, h = m_locked_h;\n'
         '  if (Is3DcFormat(m_desc.Format)) {\n'
         '    x = 0; y = 0; w = m_desc.Width; h = m_desc.Height;\n'
         '  }\n'
         '  const bool compressed = IsCompressedFormat(m_desc.Format);\n'
         '  const uint32_t row_pitch = D3DFormatRowPitch(m_desc.Format, m_desc.Width);\n'
         '  const uint32_t src_offset = image_lock_block_offset(x, y, m_pitch, row_pitch, m_desc.Width, compressed);\n'
         '  WMTOrigin origin{}; origin.x = x; origin.y = y; origin.z = 0;\n'
         '  WMTSize size{}; size.width = w; size.height = h; size.depth = 1;\n'
         '  const void *src = static_cast<const uint8_t *>(m_cpu_ptr) + src_offset;\n'
         '  m_device->stageTextureUpload(\n'
         '      m_texture, m_dxmtTexture, m_mip_level, m_array_slice, origin, size, src, m_pitch, compressed,\n'
         '      /*src_slice_pitch=*/0, D3DFormatRowPitch(m_desc.Format, w)\n'
         '  );\n'
         '}\n\nHRESULT STDMETHODCALLTYPE\nMTLD3D9Surface::GetDC(HDC *phdc) {\n'),
    ), ('flushLockedUpload',)),
    ('d3d9/d3d9_texture.hpp', (
        ('  HRESULT STDMETHODCALLTYPE AddDirtyRect(const RECT *pDirtyRect) override;\n',
         '  HRESULT STDMETHODCALLTYPE AddDirtyRect(const RECT *pDirtyRect) override;\n'
         '  void flushLockedLevels() override;\n'),
    ), ('flushLockedLevels',)),
    ('d3d9/d3d9_texture.cpp', (
        ('HRESULT STDMETHODCALLTYPE\nMTLD3D9Texture::AddDirtyRect(const RECT *pDirtyRect) {\n',
         'void\nMTLD3D9Texture::flushLockedLevels() {\n'
         '  static unsigned texture_logged = 0;\n'
         '  if (texture_logged++ < 8) {\n'
         '    unsigned locked = 0;\n'
         '    for (auto &level : m_levels)\n'
         '      if (level.ptr() && level.ptr()->locked()) ++locked;\n'
         '    Logger::warn(str::format("[d9-locked-flush] texture levels=", m_levels.size(), " locked=", locked));\n'
         '  }\n'
         '  for (auto &level : m_levels)\n'
         '    if (level.ptr() && level.ptr()->locked())\n'
         '      level.ptr()->flushLockedUpload();\n'
         '}\n\nHRESULT STDMETHODCALLTYPE\nMTLD3D9Texture::AddDirtyRect(const RECT *pDirtyRect) {\n'),
    ), ('flushLockedLevels',)),
    # iOS pages are 16 KB but DXMT_PAGE_SIZE is 4096.  The mirror is wrapped by
    # newBufferWithBytesNoCopy, which needs a host-page aligned pointer and
    # length; a 4 KB aligned mirror fails there, ensureMirror frees it silently
    # and every LockRect of a MANAGED texture returns INVALIDCALL (SDL2 2.0.3).
    ('d3d9/d3d9_texture.cpp', (
        ('    m_mirrorBacking = guest_alloc(total_bytes, DXMT_PAGE_SIZE);\n'
         '    if (!m_mirrorBacking)\n'
         '      return;\n'
         '    std::memset(m_mirrorBacking, 0, total_bytes);\n'
         '\n'
         '    WMTBufferInfo binfo{};\n'
         '    binfo.length = total_bytes;\n',
         '    const size_t page = guest_page_size();\n'
         '    const size_t mirror_alloc = (total_bytes + page - 1) & ~(page - 1);\n'
         '    m_mirrorBacking = guest_alloc(mirror_alloc, page);\n'
         '    if (!m_mirrorBacking) {\n'
         '      Logger::warn(str::format("[d9-mirror] guest_alloc failed bytes=", mirror_alloc));\n'
         '      return;\n'
         '    }\n'
         '    std::memset(m_mirrorBacking, 0, mirror_alloc);\n'
         '\n'
         '    WMTBufferInfo binfo{};\n'
         '    binfo.length = mirror_alloc;\n'),
        ('    m_mirrorBuffer = m_device->metalDevice().newBuffer(binfo);\n'
         '    if (m_mirrorBuffer == nullptr) {\n',
         '    m_mirrorBuffer = m_device->metalDevice().newBuffer(binfo);\n'
         '    if (m_mirrorBuffer == nullptr) {\n'
         '      Logger::warn(str::format("[d9-mirror] newBuffer failed ptr=", (uintptr_t)m_mirrorBacking,\n'
         '          " bytes=", mirror_alloc));\n'),
        ('  return m_levels[Level]->LockRect(pLockedRect, pRect, Flags);\n}\n',
         '  HRESULT hr = m_levels[Level]->LockRect(pLockedRect, pRect, Flags);\n'
         '  static unsigned lock_logged = 0;\n'
         '  if (lock_logged < 8 && (FAILED(hr) || lock_logged < 2)) {\n'
         '    ++lock_logged;\n'
         '    Logger::warn(str::format(\n'
         '        "[d9-lock-result] hr=", (uint32_t)hr, " pool=", (uint32_t)m_pool, " usage=", (uint32_t)m_usage,\n'
         '        " fmt=", (uint32_t)m_format, " size=", m_width, "x", m_height, " level=", Level,\n'
         '        " flags=", (uint32_t)Flags, " rect=", pRect ? 1 : 0, " mirror=", m_mirrorBacking ? 1 : 0));\n'
         '  }\n'
         '  return hr;\n}\n'),
    ), ('d9-mirror', 'd9-lock-result', 'guest_page_size()')),
    ('d3d9/d3d9_cube_texture.cpp', (
        ('    m_mirrorBacking = guest_alloc(total_bytes, DXMT_PAGE_SIZE);\n'
         '    if (!m_mirrorBacking)\n'
         '      return;\n'
         '    std::memset(m_mirrorBacking, 0, total_bytes);\n'
         '\n'
         '    WMTBufferInfo binfo{};\n'
         '    binfo.length = total_bytes;\n',
         '    const size_t page = guest_page_size();\n'
         '    const size_t mirror_alloc = (total_bytes + page - 1) & ~(page - 1);\n'
         '    m_mirrorBacking = guest_alloc(mirror_alloc, page);\n'
         '    if (!m_mirrorBacking)\n'
         '      return;\n'
         '    std::memset(m_mirrorBacking, 0, mirror_alloc);\n'
         '\n'
         '    WMTBufferInfo binfo{};\n'
         '    binfo.length = mirror_alloc;\n'),
    ), ('guest_page_size()',)),
    ('d3d9/d3d9_device.cpp', (
        ('    void *backing = guest_alloc(bytes, DXMT_PAGE_SIZE);\n'
         '    if (!backing)\n'
         '      return 0;\n',
         '    void *backing = guest_alloc(bytes, guest_page_size());\n'
         '    if (!backing)\n'
         '      return 0;\n'),
    ), ('guest_alloc(bytes, guest_page_size())',)),
    ('d3d9/d3d9_device.cpp', (
        ('#include "d3d9_device.hpp"\n',
         '#include <cstdint>\n#include "d3d9_device.hpp"\n'),
        ('enum { kLeaf2D = 0, kLeafCube = 1, kLeafVolume = 2 };\nstd::atomic<const void *> g_textureLeafVtable[3] = {};\n',
         'enum { kLeaf2D = 0, kLeafCube = 1, kLeafVolume = 2 };\nstd::atomic<const void *> g_textureLeafVtable[3] = {};\nstd::atomic<const void *> g_textureLeafObject[3] = {};\n'),
        ('void\ncaptureTextureLeafVtable(IDirect3DBaseTexture9 *leaf, unsigned index) {\n'
         '  g_textureLeafVtable[index].store(*reinterpret_cast<const void *const *>(leaf), std::memory_order_relaxed);\n'
         '}\n',
         'void\ncaptureTextureLeafVtable(IDirect3DBaseTexture9 *leaf, unsigned index) {\n'
         '  const void *vtable = *reinterpret_cast<const void *const *>(leaf);\n'
         '  g_textureLeafObject[index].store(leaf, std::memory_order_relaxed);\n'
         '  g_textureLeafVtable[index].store(vtable, std::memory_order_relaxed);\n'
         '  static unsigned capture_logged = 0;\n'
         '  if (capture_logged++ < 8)\n'
         '    Logger::warn(str::format(\n'
         '        "[d9-locked-flush] capture idx=", index, " ptr=", (uintptr_t)leaf,\n'
         '        " vtable=", (uintptr_t)vtable));\n'
         '}\n'),
        ('  if (!iface)\n'
         '    return nullptr;\n'
         '  const void *vtable = *reinterpret_cast<const void *const *>(iface);\n',
         '  if (!iface)\n'
         '    return nullptr;\n'
         '  // The native handle table already validated the object identity.  Match\n'
         '  // the canonical pointer as a fallback for the 32-bit bridge, where the\n'
         '  // interface pointer can reach this side with a different vtable value.\n'
         '  if (iface == g_textureLeafObject[kLeaf2D].load(std::memory_order_relaxed))\n'
         '    return static_cast<MTLD3D9Texture *>(static_cast<IDirect3DTexture9 *>(iface));\n'
         '  if (iface == g_textureLeafObject[kLeafCube].load(std::memory_order_relaxed))\n'
         '    return static_cast<MTLD3D9CubeTexture *>(static_cast<IDirect3DCubeTexture9 *>(iface));\n'
         '  if (iface == g_textureLeafObject[kLeafVolume].load(std::memory_order_relaxed))\n'
         '    return static_cast<MTLD3D9VolumeTexture *>(static_cast<IDirect3DVolumeTexture9 *>(iface));\n'
         '  const void *vtable = *reinterpret_cast<const void *const *>(iface);\n'),
        ('  // Identify the texture without calling through its app-visible vtable (which an\n'
         '  // overlay may have wrapped, or an app deliberately corrupted): an unrecognised\n'
         '  // non-null pointer is treated as "no texture", matching wined3d.\n'
         '  MTLD3D9CommonTexture *common = commonTextureFromBound(pTexture);\n',
         '  // Identify the texture without calling through its app-visible vtable (which an\n'
         '  // overlay may have wrapped, or an app deliberately corrupted): an unrecognised\n'
         '  // non-null pointer is treated as "no texture", matching wined3d.\n'
         '  static unsigned settex_entry_logged = 0;\n'
         '  if (settex_entry_logged++ < 8) {\n'
         '    const void *bound_vtable =\n'
         '        pTexture ? *reinterpret_cast<const void *const *>(pTexture) : nullptr;\n'
         '    Logger::warn(str::format(\n'
         '        "[d9-locked-flush] entry slot=", slot, " ptr=", (uintptr_t)pTexture,\n'
         '        " vtable=", (uintptr_t)bound_vtable,\n'
         '        " cap2d=", (uintptr_t)g_textureLeafVtable[kLeaf2D].load(std::memory_order_relaxed),\n'
         '        " obj2d=", (uintptr_t)g_textureLeafObject[kLeaf2D].load(std::memory_order_relaxed)));\n'
         '  }\n'
         '  MTLD3D9CommonTexture *common = commonTextureFromBound(pTexture);\n'),
        ('  }\n  // Recording arm AFTER the validation gates above (DXVK validates\n',
         '  }\n  if (common) {\n'
         '    static unsigned settex_logged = 0;\n'
         '    if (settex_logged++ < 8)\n'
         '      Logger::warn(str::format(\n'
         '          "[d9-locked-flush] matched ptr=", (uintptr_t)pTexture, " owner=", (uintptr_t)common->deviceRaw(),\n'
         '          " this=", (uintptr_t)this, " type=", common->commonTextureType()));\n'
         '    common->flushLockedLevels();\n'
         '  }\n'
         '  // Recording arm AFTER the validation gates above (DXVK validates\n'),
    ), ('flushLockedLevels', 'd9-locked-flush')),
)
for relative, pairs, markers in d3d9_files:
    out = output_root / relative
    src = (out if out.exists() else source_root / relative).read_text(encoding='utf-8')
    for original, replacement in pairs:
        if src.count(original) != 1:
            raise SystemExit(f'{relative}: expected 1 match, found {src.count(original)}: {original[:60]!r}')
        src = src.replace(original, replacement)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding='utf-8')
    written = out.read_text(encoding='utf-8')
    for marker in markers:
        if marker not in written:
            raise SystemExit(f'{relative}: {marker!r} missing from the written file')
    print(f'iOS {sys.argv[3]} d3d9 locked-texture flush: {relative}')

# ---- Madeira-15.6: D3D9 performance fixes ported from iediot/Madeira -------
# These are dxmt *source* changes upstream carries as commits on its own dxmt
# fork; carrying them here keeps the submodule pointer on willfaust/dxmt.
perf_files = (
    # iediot/Madeira 2ef9f07: a draw uploaded only the registers the sticky
    # high-water mark had recorded, so a constant the shader reads but the app
    # never set was left holding a previous draw's value. Size the upload by the
    # shader's own highest constant register, and key the constant cache by the
    # shader itself so two shaders with different reads never share an upload.
    ('d3d9/d3d9_device.cpp', (
        ('  uint32_t vs_f_regs = resolve_const_f_extent(pod.vs_const_f_max, vs_uses_relative, vs_file_size);\n'
         '  uint32_t ps_f_regs = resolve_const_f_extent(pod.ps_const_f_max, ps_uses_relative, D3D9_MAX_PS_CONST_F);\n',
         '  // Madeira-15.6 (iediot 2ef9f07): cover every register the shader reads.\n'
         '  const uint32_t vs_meta_max = (!ffp_vs && vs) ? vs->metadata().max_float_const_index : 0u;\n'
         '  const uint32_t ps_meta_max = ps ? ps->metadata().max_float_const_index : 0u;\n'
         '  uint32_t vs_f_regs = resolve_const_f_extent(std::max<uint32_t>(pod.vs_const_f_max, vs_meta_max), vs_uses_relative, vs_file_size);\n'
         '  uint32_t ps_f_regs = resolve_const_f_extent(std::max<uint32_t>(pod.ps_const_f_max, ps_meta_max), ps_uses_relative, D3D9_MAX_PS_CONST_F);\n'),
        ('  const void *vs_defs_key =\n'
         '      !ffp_vs ? (vs_uses_relative ? static_cast<const void *>(vs) : nullptr) : static_cast<const void *>(&kFfpConstKey);\n',
         '  const void *vs_defs_key =\n'
         '      !ffp_vs ? static_cast<const void *>(vs) : static_cast<const void *>(&kFfpConstKey);\n'),
        ('  const void *ps_defs_key =\n'
         '      ps ? (ps_uses_relative ? static_cast<const void *>(ps) : nullptr) : static_cast<const void *>(&kFfpConstKey);\n',
         '  const void *ps_defs_key =\n'
         '      ps ? static_cast<const void *>(ps) : static_cast<const void *>(&kFfpConstKey);\n'),
    ), ('max_float_const_index',)),
    # iediot/Madeira e2e36eb: the dynamic-buffer trim kept a flat 64 entries,
    # which for a buffer whose entries are megabytes pins far more than needed.
    # Reserve about 8 MB per buffer instead (clamped to the old 64..4096 range).
    ('dxmt/dxmt_dynamic.cpp', (
        ('#include "dxmt_dynamic.hpp"\n',
         '#include <algorithm>\n#include "dxmt_dynamic.hpp"\n'),
        ('    enum { DYN_KEEP_ELIGIBLE = 64 };\n    if (en > DYN_KEEP_ELIGIBLE) {\n',
         '    // Madeira-15.6 (iediot e2e36eb): ~8 MB per buffer, not a flat 64 entries.\n'
         '    unsigned DYN_KEEP_ELIGIBLE = 64;\n'
         '    if (en && !fifo.empty() && fifo.front().allocation.ptr()) {\n'
         '      const uint64_t entry_bytes = std::max<uint64_t>(fifo.front().allocation->census_bytes_, 256);\n'
         '      DYN_KEEP_ELIGIBLE = (unsigned)std::clamp<uint64_t>((8ull << 20) / entry_bytes, 64, 4096);\n'
         '    }\n'
         '    if (en > DYN_KEEP_ELIGIBLE) {\n'),
    ), ('DYN_KEEP_ELIGIBLE',)),
)
for relative, pairs, markers in perf_files:
    out = output_root / relative
    src = (out if out.exists() else source_root / relative).read_text(encoding='utf-8')
    for original, replacement in pairs:
        if src.count(original) != 1:
            raise SystemExit(f'{relative}: perf fix expected 1 match, found {src.count(original)}: {original[:60]!r}')
        src = src.replace(original, replacement)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding='utf-8')
    written = out.read_text(encoding='utf-8')
    for marker in markers:
        if marker not in written:
            raise SystemExit(f'{relative}: {marker!r} missing from the written file')
    print(f'iOS {sys.argv[3]} d3d9 performance (iediot): {relative}')

# Madeira-15.6: a Reset that fails leaves the device in NotReset, and a game
# asking TestCooperativeLevel every frame then retries Reset forever with no
# picture (BaldrSky, 2026-10-28: 1404 Resets in 23 s, audio playing, no present).
# Both failure points returned silently; name them once each.
diag_files = (
    ('d3d9/d3d9_device.cpp', (
        ('  if (!m_isEx && m_losableResourceCount.load(std::memory_order_relaxed) != 0) {\n'
         '    m_deviceState.store(DeviceState::NotReset, std::memory_order_relaxed);\n'
         '    return D3DERR_INVALIDCALL;\n'
         '  }\n',
         '  /* Madeira-15.6: BaldrSky switches to fullscreen holding one implicit\n'
         '   * backbuffer reference and one 800x600 DEFAULT-pool surface, so the spec\n'
         '   * gate refuses Reset for ever and the picture freezes with audio and input\n'
         '   * still running. Those resources are not part of the swapchain we are about\n'
         '   * to recreate, so letting Reset through cannot corrupt them: the game keeps\n'
         '   * using its own surfaces, and the new backbuffer arrives as usual.\n'
         '   * MADEIRA_D9_RESET_STRICT=1 restores the spec behaviour. */\n'
         '  /* Only an explicit "1" brings the spec gate back. Presence alone must not:\n'
         '   * the first device run logged "refused" with the patch installed, which\n'
         '   * means something in the process environment carries this name with a\n'
         '   * value that is not "1" -- so test the value, and print it. */\n'
         '  const char *madeira_reset_env = getenv("MADEIRA_D9_RESET_STRICT");\n'
         '  const bool madeira_reset_strict =\n'
         '      madeira_reset_env && madeira_reset_env[0] == \'1\' && !madeira_reset_env[1];\n'
         '  if (!m_isEx && m_losableResourceCount.load(std::memory_order_relaxed) != 0 &&\n'
         '      !madeira_reset_strict) {\n'
         '    static unsigned reset_proceeds;\n'
         '    if (reset_proceeds++ < 4)\n'
         '      Logger::warn(str::format("[d9-reset] proceeding with losable=",\n'
         '          m_losableResourceCount.load(std::memory_order_relaxed), " ex=", m_isEx,\n'
         '          " strict_env=", madeira_reset_env ? madeira_reset_env : "(unset)"));\n'
         '  } else if (!m_isEx && m_losableResourceCount.load(std::memory_order_relaxed) != 0) {\n'
         '    static unsigned reset_refusals;\n'
         '    if (reset_refusals++ < 8)\n'
         '      Logger::warn(str::format("[d9-reset] refused: losable=",\n'
         '          m_losableResourceCount.load(std::memory_order_relaxed), " ex=", m_isEx,\n'
         '          " strict_env=", madeira_reset_env ? madeira_reset_env : "(unset)"));\n'
         '    m_deviceState.store(DeviceState::NotReset, std::memory_order_relaxed);\n'
         '    return D3DERR_INVALIDCALL;\n'
         '  }\n'),
        ('  if (FAILED(hr)) {\n'
         '    // Non-Ex only: an Ex device never enters NotReset (wine/DXVK gate the store\n',
         '  if (FAILED(hr)) {\n'
         '    static unsigned reset_failures;\n'
         '    if (reset_failures++ < 8)\n'
         '      Logger::warn(str::format("[d9-reset] swapchain failed hr=", (uint32_t)hr,\n'
         '          " w=", pPresentationParameters->BackBufferWidth,\n'
         '          " h=", pPresentationParameters->BackBufferHeight));\n'
         '    // Non-Ex only: an Ex device never enters NotReset (wine/DXVK gate the store\n'),
    ), ('d9-reset')),
    # Madeira-15.6: [d9-reset] reports how many DEFAULT-pool resources the app
    # still holds (BaldrSky fullscreen: losable=2, Reset refused three times,
    # then no more frames -- the game is wedged in NotReset, audio and input
    # alive). The count alone does not say what those two are, and the gate must
    # not be relaxed on a guess. Log every count edge with its byte size: 0
    # bytes is an implicit backbuffer/depth-stencil or a state block, a real
    # size is a texture or surface. Capped so a long session stays readable.
    ('d3d9/d3d9_device.hpp', (
        # Which class is holding the resource: the byte count alone cannot tell a
        # state block from an implicit surface when both report 0 (BaldrSky's
        # fullscreen Reset is blocked by exactly one 0-byte and one 32 KB entry).
        ('  void\n'
         '  onLosableResourceCreated(int64_t bytes = 0) {\n'
         '    m_losableResourceCount.fetch_add(1, std::memory_order_relaxed);\n',
         '  const char *madeira_last_kind = "?";\n'
         '  void madeiraLosableKind(const char *k) { madeira_last_kind = k; }\n'
         '  void\n'
         '  onLosableResourceCreated(int64_t bytes = 0) {\n'
         '    m_losableResourceCount.fetch_add(1, std::memory_order_relaxed);\n'
         '    {\n'
         '      static unsigned madeira_los_logs;\n'
         '      uint32_t now = m_losableResourceCount.load(std::memory_order_relaxed);\n'
         '      if (madeira_los_logs++ < 200)\n'
         '        Logger::warn(str::format("[d9-los]+ bytes=", (long long)bytes,\n'
         '            " live=", now, " kind=", madeira_last_kind));\n'
         '      madeira_last_kind = "?";\n'
         '    }\n'),
        ('  onLosableResourceDestroyed(int64_t bytes = 0) {\n'
         '    m_losableResourceCount.fetch_sub(1, std::memory_order_relaxed);\n',
         '  onLosableResourceDestroyed(int64_t bytes = 0) {\n'
         '    m_losableResourceCount.fetch_sub(1, std::memory_order_relaxed);\n'
         '    {\n'
         '      static unsigned madeira_los_dlogs;\n'
         '      uint32_t now = m_losableResourceCount.load(std::memory_order_relaxed);\n'
         '      if (madeira_los_dlogs++ < 200)\n'
         '        Logger::warn(str::format("[d9-los]- bytes=", (long long)bytes,\n'
         '            " live=", now));\n'
         '    }\n'),
    ), ('d9-los')),
    # Where the second fullscreen switch stops. The app-side layout provably runs
    # again on every mode change (host frame, desktop rect, px_to_pt all log), and
    # the only thing DXMT updates on the next Present is the layer's drawable size
    # -- so "frozen" means either no Present at all or a Present rejected by the
    # device state. These say which: a gate rejection, a Reset stuck draining the
    # GPU, or a Reset that finished.
    ('d3d9/d3d9_swapchain.cpp', (
        # Is Present even called after the freeze? BaldrSky's three Resets all
        # complete (gpu drain begin/done, then done) and no gate rejection
        # follows, so "frozen" is either "the game stopped presenting" or
        # "Present returns but nothing reaches the layer". Count the calls.
        ('  D3D9_CENSUS_FRAME(D3D9_CENSUS_MTLD3D9SwapChain_Present);\n',
         '  D3D9_CENSUS_FRAME(D3D9_CENSUS_MTLD3D9SwapChain_Present);\n'
         '  { static unsigned madeira_present_calls; unsigned n = madeira_present_calls++;\n'
         '    if (n < 3 || (n % 300) == 0)\n'
         '      Logger::warn(str::format("[d9-present] call n=", n)); }\n'),
        ('  if (HRESULT state = m_device->presentStateGate(); state != D3D_OK)\n'
         '    return state;\n',
         '  if (HRESULT state = m_device->presentStateGate(); state != D3D_OK) {\n'
         '    static unsigned madeira_present_gate;\n'
         '    if (madeira_present_gate++ < 8 || (madeira_present_gate % 256) == 0)\n'
         '      Logger::warn(str::format("[d9-present] rejected state=",\n'
         '          (uint32_t)state, " n=", madeira_present_gate));\n'
         '    return state;\n'
         '  }\n'),
    ), ('d9-present')),
    ('d3d9/d3d9_device.cpp', (
        ('    commitCurrentChunkTimed();\n'
         '    m_dxmtQueue->WaitCPUFence(seq);\n'
         '  }\n',
         '    Logger::warn("[d9-reset] gpu drain begin");\n'
         '    commitCurrentChunkTimed();\n'
         '    m_dxmtQueue->WaitCPUFence(seq);\n'
         '    Logger::warn("[d9-reset] gpu drain done");\n'
         '  }\n'),
        ('  m_deviceState.store(DeviceState::Ok, std::memory_order_relaxed);\n',
         '  m_deviceState.store(DeviceState::Ok, std::memory_order_relaxed);\n'
         '  Logger::warn("[d9-reset] done");\n'),
    ), ('d9-reset] gpu drain begin')),
    # Madeira-15.6: the D3D11 argument-buffer ring is a 4 MB CPU-visible block
    # handed to Metal as a no-copy mapping. On device that mapping is refused
    # ([d11-newbuf] NIL len=4194304 cpu=1), so the ring hands out a block with
    # no buffer, and every draw binds index 30 = null ([d11-argbuf] NULL
    # size=104): the whole session submits invalid command buffers and shows
    # nothing. Metal cannot refuse storage it allocates itself, so fall back to
    # a Metal-owned buffer and use its mapping as the CPU view. The block has to
    # remember that the mapping is not ours to free.
    ('dxmt/dxmt_ring_bump_allocator.hpp', (
        ('#include <cassert>\n', '#include <cassert>\n#include <cstdio>\n'),
        ('      buffer = nullptr;\n'
         '      if (mapped_address) {\n'
         '        free(mapped_address);\n'
         '        mapped_address = nullptr;\n'
         '      }\n',
         '      buffer = nullptr;\n'
         '      if (mapped_address) {\n'
         '        /* Madeira-15.6: a fallback block maps Metal-owned storage, so only\n'
         '         * our own malloc may go back. */\n'
         '        if (owns_mapped)\n'
         '          free(mapped_address);\n'
         '        mapped_address = nullptr;\n'
         '      }\n'),
        ('      census_owner = move.census_owner;\n'
         '      move.census_bytes = 0;\n'
         '      move.mapped_address = nullptr;\n',
         '      census_owner = move.census_owner;\n'
         '      owns_mapped = move.owns_mapped;\n'
         '      move.census_bytes = 0;\n'
         '      move.mapped_address = nullptr;\n'),
        ('    uint64_t census_bytes = 0;                             /* ml677 */\n'
         '    MemOwner census_owner = MEMOWN_STAGING_RING;           /* ml1243 */\n',
         '    uint64_t census_bytes = 0;                             /* ml677 */\n'
         '    MemOwner census_owner = MEMOWN_STAGING_RING;           /* ml1243 */\n'
         '    bool owns_mapped = true;                               /* Madeira-15.6 */\n'),
        ('    block.buffer = device_.newBuffer(info);\n'
         '    block.gpu_address = info.gpu_address;\n',
         '    block.buffer = device_.newBuffer(info);\n'
         '    block.gpu_address = info.gpu_address;\n'
         '    if (!block.buffer && block.mapped_address) {\n'
         '      /* Madeira-15.6: the no-copy mapping was refused. Let Metal own the\n'
         '       * storage and take ITS mapping as the CPU view, so the ring still\n'
         '       * writes where the GPU reads. */\n'
         '      fprintf(stderr, "[d11-ring] no-copy refused (%llu bytes); using a '
         'Metal-owned buffer%c",\n'
         '              (unsigned long long)block_size, 10);\n'
         '      free(block.mapped_address);\n'
         '      WMTBufferInfo again{};\n'
         '      again.options = buffer_info_;\n'
         '      again.length = block_size;\n'
         '      block.buffer = device_.newBuffer(again);\n'
         '      block.mapped_address = again.memory.ptr;\n'
         '      block.gpu_address = again.gpu_address;\n'
         '      block.owns_mapped = false;\n'
         '    }\n'),
    ), ('d11-ring')),
    # The swapchain-level counter never printed for BaldrSky even though the
    # session renders and its Reset diagnostics do print, so the game's presents
    # do not reach MTLD9SwapChain::Present through this path. Count at the device
    # entry points instead -- Present and PresentEx are what the guest calls, and
    # both forward to the chain only when they get that far.
    ('d3d9/d3d9_device.cpp', (
        ('  D3D9_CENSUS(D3D9_CENSUS_MTLD3D9Device_Present);\n',
         '  D3D9_CENSUS(D3D9_CENSUS_MTLD3D9Device_Present);\n'
         '  { static unsigned madeira_present_dev; unsigned n = madeira_present_dev++;\n'
         '    if (n < 3 || (n % 300) == 0)\n'
         '      Logger::warn(str::format("[d9-present] dev Present n=", n)); }\n'),
        ('  D3D9_CENSUS(D3D9_CENSUS_MTLD3D9Device_PresentEx);\n',
         '  D3D9_CENSUS(D3D9_CENSUS_MTLD3D9Device_PresentEx);\n'
         '  { static unsigned madeira_presentex_dev; unsigned n = madeira_presentex_dev++;\n'
         '    if (n < 3 || (n % 300) == 0)\n'
         '      Logger::warn(str::format("[d9-present] dev PresentEx n=", n)); }\n'),
    ), ('d9-present] dev Present')),
    # Backgrounding invalidates the layer's drawables, and the presenter kept
    # presenting into them: TheShell 2026-10-29 logged 45 "Invalid Resource"
    # command-buffer failures between "resigned active" and "became active", and
    # the picture stayed frozen after coming back. Returning nil here is the same
    # frame-skip the RAW mode below already uses (no drawable touched, the PE side
    # skips the blit), so nothing is presented into an invalid drawable while the
    # app is not active; the app flips the flag from its lifecycle notifications.
    ('winemetal/unix/winemetal_unix.c', (
        ('static NTSTATUS\n_MetalLayer_nextDrawable(void *obj) {\n'
         '  struct unixcall_generic_obj_obj_ret *params = obj;\n',
         '/* Madeira-15.6: set from the app (madeira_set_app_active). */\n'
         '_Atomic int madeira_app_active = 1;\n'
         'void madeira_set_app_active(int active)\n'
         '{\n'
         '  atomic_store_explicit(&madeira_app_active, active ? 1 : 0, memory_order_relaxed);\n'
         '}\n'
         'int madeira_get_app_active(void)\n'
         '{\n'
         '  return atomic_load_explicit(&madeira_app_active, memory_order_relaxed);\n'
         '}\n'
         '\n'
         'static NTSTATUS\n'
         '_MetalLayer_nextDrawable(void *obj) {\n'
         '  struct unixcall_generic_obj_obj_ret *params = obj;\n'
         '  if (!atomic_load_explicit(&madeira_app_active, memory_order_relaxed)) {\n'
         '    params->ret = 0;   /* backgrounded: skip this frame, touch no drawable */\n'
         '    { static unsigned madeira_paused;\n'
         '      if (madeira_paused++ < 8 || (madeira_paused % 256) == 0)\n'
         '        dprintf(STDERR_FILENO, "[present-pause] backgrounded, frame skipped (n=%u)%c",\n'
         '                madeira_paused, 10); }\n'
         '    return STATUS_SUCCESS;\n'
         '  }\n'),
    ), ('madeira_set_app_active')),
    # The two silent ways a draw disappears: the batched-draw resolver rejects it,
    # or its pipeline object never linked. BaldrSky's title screen is a white
    # background with black text on a PC, and the game issues no white clear at
    # all (64 sampled clears: one opaque black, 63 pure black), so the white must
    # be geometry -- and a silently skipped draw is exactly how a background
    # layer goes missing while the text above it still renders.
    ('d3d9/d3d9_device.cpp', (
        ('          if (!this->ResolveBatchedDrawForChunk(bd, res, chunk_seq, chunk_coherent_id, const_cache, resolve_cache)) {\n'
         '            continue;\n'
         '          }\n',
         '          if (!this->ResolveBatchedDrawForChunk(bd, res, chunk_seq, chunk_coherent_id, const_cache, resolve_cache)) {\n'
         '            static unsigned madeira_skip_resolve;\n'
         '            if (madeira_skip_resolve++ < 32)\n'
         '              Logger::warn(str::format("[d9-skip] resolve-failed n=", madeira_skip_resolve));\n'
         '            continue;\n'
         '          }\n'),
        ('          res.resolved_pso = res.resolved_pso_task->state().handle;\n'
         '          if (res.resolved_pso == 0) {\n'
         '            continue; // link failed: skip\n'
         '          }\n',
         '          res.resolved_pso = res.resolved_pso_task->state().handle;\n'
         '          if (res.resolved_pso == 0) {\n'
         '            static unsigned madeira_skip_pso;\n'
         '            if (madeira_skip_pso++ < 32)\n'
         '              Logger::warn(str::format("[d9-skip] pso-link-failed n=", madeira_skip_pso));\n'
         '            continue; // link failed: skip\n'
         '          }\n'),
    ), ('d9-skip]')),
    # Render-state trace for BaldrSky's missing white text box.  Chapter 1 opens
    # with a text window on an opaque white quad; here the scene behind it shows
    # through while the text itself still renders.  No clear is issued for that
    # white ([d9-clear] shows one black clear and 63 colour=0 ones), [d9-skip]
    # never fires, so the loss is in the box's own draw: a render state that
    # makes a quad write nothing is the prime suspect (COLORWRITEENABLE 0, a
    # blend that keeps the destination, clipping off with a zero-area scissor).
    # Only the states that can do that are logged, only on change, first 600.
    ('d3d9/d3d9_device.cpp', (
        ('MTLD3D9Device::SetRenderState(D3DRENDERSTATETYPE State, DWORD Value) {\n'
         '  D3D9_CENSUS(D3D9_CENSUS_MTLD3D9Device_SetRenderState);\n',
         'MTLD3D9Device::SetRenderState(D3DRENDERSTATETYPE State, DWORD Value) {\n'
         '  D3D9_CENSUS(D3D9_CENSUS_MTLD3D9Device_SetRenderState);\n'
         '  if (State == D3DRS_ALPHATESTENABLE || State == D3DRS_ALPHABLENDENABLE ||\n'
         '      State == D3DRS_SRCBLEND || State == D3DRS_DESTBLEND ||\n'
         '      State == D3DRS_BLENDOP || State == D3DRS_COLORWRITEENABLE ||\n'
         '      State == D3DRS_ZWRITEENABLE || State == D3DRS_CLIPPING) {\n'
         '    static unsigned madeira_rs_logged;\n'
         '    if (madeira_rs_logged++ < 600)\n'
         '      Logger::warn(str::format("[d9-rs] n=", madeira_rs_logged,\n'
         '          " state=", (uint32_t)State, " value=", Value));\n'
         '  }\n'),
    ), ('d9-rs]')),
    # Which clears the game actually asks for, and with what colour. BaldrSky's
    # title is a white screen with black text on a PC; here the previous scene
    # shows through. The mismatched-size drop was NOT it ([d9-clear] never fired),
    # so the question is whether a white clear is issued at all: no clear means
    # the white is a drawn quad and something else eats it; a clear that is
    # issued but never reaches the GPU points at the encoder/merge path.
    ('d3d9/d3d9_device.cpp', (
        ('  MTLD3D9Surface *ds = m_depthStencilSurface.ptr();\n'
         '  // No DS bound + Z/Stencil flag -> INVALIDCALL (DXVK).\n',
         '  { static unsigned madeira_clear_logged;\n'
         '    if (madeira_clear_logged < 64)\n'
         '    {\n'
         '      MTLD3D9Surface *rt0_log = m_renderTargets[0].ptr();\n'
         '      madeira_clear_logged++;\n'
         '      Logger::warn(str::format("[d9-clear] flags=", Flags, " color=", (uint32_t)Color,\n'
         '          " rects=", Count, " rt0=", rt0_log ? rt0_log->desc().Width : 0, "x",\n'
         '          rt0_log ? rt0_log->desc().Height : 0, " vp=", m_viewport.Width, "x", m_viewport.Height));\n'
         '    }\n'
         '  }\n'
         '  MTLD3D9Surface *ds = m_depthStencilSurface.ptr();\n'
         '  // No DS bound + Z/Stencil flag -> INVALIDCALL (DXVK).\n'),
    ), ('d9-clear] flags=')),
    # wined3d drops a combined TARGET+Z/STENCIL clear when the bound DS is
    # smaller than RT0. Native D3D9 does not: each attachment is cleared at its
    # own size, which the code below already does. BaldrSky's title screen came
    # up showing the previous scene instead of its white background, and a
    # silently dropped clear is the shape of that (2026-10-28).
    ('d3d9/d3d9_device.cpp', (
        ('  if (ds && (Flags & D3DCLEAR_TARGET) && (Flags & (D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL))) {\n'
         '    MTLD3D9Surface *rt0 = m_renderTargets[0].ptr();\n'
         '    if (rt0 && (ds->desc().Width < rt0->desc().Width || ds->desc().Height < rt0->desc().Height))\n'
         '      return D3D_OK;\n'
         '  }\n',
         '  if (ds && (Flags & D3DCLEAR_TARGET) && (Flags & (D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL))) {\n'
         '    MTLD3D9Surface *rt0 = m_renderTargets[0].ptr();\n'
         '    if (rt0 && (ds->desc().Width < rt0->desc().Width || ds->desc().Height < rt0->desc().Height)) {\n'
         '      /* Madeira-15.6: wined3d drops this clear whole; native D3D9 clears each\n'
         '       * attachment at its own size, and the paths below do exactly that. The\n'
         '       * drop left BaldrSky showing the previous scene behind its title. */\n'
         '      static unsigned madeira_clear_mismatch;\n'
         '      if (madeira_clear_mismatch++ < 8)\n'
         '        Logger::warn(str::format("[d9-clear] mismatched sizes, keeping the clear: ds=",\n'
         '            ds->desc().Width, "x", ds->desc().Height, " rt0=", rt0->desc().Width, "x", rt0->desc().Height));\n'
         '    }\n'
         '  }\n'),
    ), ('d9-clear] mismatched sizes')),
    # A command buffer that never retires stops the finish thread, which stops
    # every Present downstream -- silently. Poll instead of blocking, and name
    # the frame that is stuck (TheShell: 4 Invalid Resource frames, then Present
    # stopped at 10 and the picture froze with the app still alive).
    ('dxmt/dxmt_command_queue.cpp', (
        # <thread> for the poll above; the file already includes <chrono>.
        ('#include <chrono>\n', '#include <chrono>\n#include <thread>\n'),
        ('    if (chunk.attached_cmdbuf.status() <= WMTCommandBufferStatusScheduled) {\n'
         '      chunk.attached_cmdbuf.waitUntilCompleted();\n'
         '    }\n',
         '    if (chunk.attached_cmdbuf.status() <= WMTCommandBufferStatusScheduled) {\n'
         '      unsigned madeira_wait_ms = 0;\n'
         '      while (chunk.attached_cmdbuf.status() <= WMTCommandBufferStatusScheduled) {\n'
         '        std::this_thread::sleep_for(std::chrono::milliseconds(10));\n'
         '        madeira_wait_ms += 10;\n'
         '        if ((madeira_wait_ms % 2000) == 0) {\n'
         '          static unsigned madeira_gpu_wait_said;\n'
         '          if (madeira_gpu_wait_said++ < 20)\n'
         '            ERR("[gpu-wait] frame=", chunk.frame_, " status=",\n'
         '                (int)chunk.attached_cmdbuf.status(), " waited_ms=", madeira_wait_ms);\n'
         '        }\n'
         '      }\n'
         '    }\n'),
    ), ('gpu-wait] frame=')),
    # Map must not hand out a NULL pointer with S_OK. Dead Cells (FNA, D3D11)
    # writes through the mapped pointer at its own ring offset and faulted at
    # 0x10004 -- 0x10000 (the 64 KB Windows allocation granularity) plus the 4-byte
    # field, i.e. a NULL base plus an offset. A dynamic buffer whose Metal storage
    # has no CPU mapping (allocation refused, or a storage mode without
    # [buffer contents]) gives exactly that: pData NULL, pitches filled in, S_OK.
    ('d3d11/d3d11_context_imm.cpp', (
        ('        pMappedResource->DepthPitch = buffer_length;\n'
         '        break;\n'
         '      }\n'
         '      }\n'
         '      return S_OK;\n'
         '    }\n',
         '        pMappedResource->DepthPitch = buffer_length;\n'
         '        break;\n'
         '      }\n'
         '      }\n'
         '      if (!pMappedResource->pData) {\n'
         '        static unsigned madeira_map_null;\n'
         '        if (madeira_map_null++ < 20)\n'
         '          Logger::warn(str::format("[d3d11-map] dynamic buffer has no CPU mapping: sub=",\n'
         '              Subresource, " len=", buffer_length));\n'
         '        return E_OUTOFMEMORY;\n'
         '      }\n'
         '      return S_OK;\n'
         '    }\n'),
    ), ('d3d11-map] dynamic buffer has no CPU mapping')),
    # Which kind each losable resource is. BaldrSky's fullscreen Reset is blocked
    # by exactly one 0-byte and one 32 KB entry, and the size cannot say whether
    # the 0-byte one is the implicit backbuffer/depth-stencil (which native D3D9
    # hands back automatically) or a state block the game forgot to release --
    # the two need opposite fixes.
    ('d3d9/d3d9_buffer.cpp', (
        ('MTLD3D9VertexBuffer::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9VertexBuffer::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("vb");\n'),
        ('MTLD3D9IndexBuffer::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9IndexBuffer::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("ib");\n'),
    ), ('madeiraLosableKind',)),
    ('d3d9/d3d9_texture.cpp', (
        ('MTLD3D9Texture::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9Texture::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("tex");\n'),
    ), ('madeiraLosableKind',)),
    ('d3d9/d3d9_cube_texture.cpp', (
        ('MTLD3D9CubeTexture::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9CubeTexture::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("cube");\n'),
    ), ('madeiraLosableKind',)),
    ('d3d9/d3d9_volume_texture.cpp', (
        ('MTLD3D9VolumeTexture::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9VolumeTexture::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("vol");\n'),
    ), ('madeiraLosableKind',)),
    ('d3d9/d3d9_state_block.cpp', (
        ('MTLD3D9StateBlock::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9StateBlock::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("state");\n'),
    ), ('madeiraLosableKind',)),
    ('d3d9/d3d9_surface.cpp', (
        ('MTLD3D9Surface::markLosable() {\n  if (!m_isLosable) {\n',
         'MTLD3D9Surface::markLosable() {\n  if (!m_isLosable) {\n    m_device->madeiraLosableKind("surf");\n'),
        ('    if (m_isImplicitLosable)\n      m_device->onLosableResourceCreated();\n',
         '    if (m_isImplicitLosable) {\n      m_device->madeiraLosableKind("implicit");\n'
         '      m_device->onLosableResourceCreated();\n    }\n'),
    ), ('madeiraLosableKind',)),
    # TheShell froze on every background round-trip on 2026-10-29: the frame loop
    # kept encoding and committing while iOS refuses background GPU work, 20
    # commits failed with kIOGPUCommandBufferCallbackErrorBackgroundExecution
    # NotPermitted (Code=7), and that error path then hit a BRK which killed the
    # process with c0000023. The last frame stayed on screen and input kept
    # working, so it read as "coming back from background froze it". Dropping the
    # chunk costs nothing visible -- the drawable is gone anyway -- and keeps iOS
    # out of it. The finish thread then sees the previous, already-completed
    # buffer and advances without waiting.
    ('dxmt/dxmt_command_queue.cpp', (
        ('#include "util_win32_compat.h"\n',
         '#include "util_win32_compat.h"\n'
         'extern "C" int madeira_get_app_active(void) __attribute__((weak));\n'
         '/* weak: the arm64ec PE build compiles this file too and has no\n'
         ' * winemetal_unix.c, so the symbol is absent there. */\n'),
        ('  auto cmdbuf = commandQueue.commandBuffer();\n',
         '  if (madeira_get_app_active && !madeira_get_app_active()) {\n'
         '    static unsigned madeira_bg_dropped;\n'
         '    if (madeira_bg_dropped++ < 8 || (madeira_bg_dropped % 256) == 0)\n'
         '      WARN("[bg-commit] dropped frame=", chunk.frame_, " n=", madeira_bg_dropped);\n'
         '    ready_for_commit.fetch_add(1, std::memory_order_release);\n'
         '    dxmt::atomic_notify_one(ready_for_commit);\n'
         '    return;\n'
         '  }\n'
         '  auto cmdbuf = commandQueue.commandBuffer();\n'
),
    ), ('bg-commit] dropped')),
)
for relative, pairs, markers in diag_files:
    out = output_root / relative
    src = (out if out.exists() else source_root / relative).read_text(encoding='utf-8')
    for original, replacement in pairs:
        if src.count(original) != 1:
            raise SystemExit(f'{relative}: reset diag expected 1 match, found {src.count(original)}')
        src = src.replace(original, replacement)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding='utf-8')
    for marker in markers:
        if marker not in out.read_text(encoding='utf-8'):
            raise SystemExit(f'{relative}: {marker} missing')
    print(f'iOS {sys.argv[3]} d3d9 reset diagnostics: {relative}')


#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Intrinsics.h"
#include "llvm/IR/DerivedTypes.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Metadata.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Verifier.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Support/VersionTuple.h"
#include "llvm/Bitcode/BitcodeReader.h"
#include "llvm/Linker/Linker.h"
#include "llvm/Passes/StandardInstrumentations.h"
#include "llvm/Transforms/Scalar/LowerExpectIntrinsic.h"
#include "llvm/Transforms/Scalar/SimplifyCFG.h"
#include "llvm/Transforms/Scalar/SROA.h"
#include "llvm/Transforms/Scalar/EarlyCSE.h"
#include "llvm/Transforms/IPO/AlwaysInliner.h"
#include "llvm/Transforms/InstCombine/InstCombine.h"
#include "llvm/Transforms/IPO/DeadArgumentElimination.h"
#include "llvm/Transforms/IPO/GlobalOpt.h"
#include "llvm/Transforms/IPO/SCCP.h"
#include "llvm/Transforms/IPO/Annotation2Metadata.h"
#include "llvm/Transforms/IPO/ForceFunctionAttrs.h"
#include "llvm/Transforms/IPO/InferFunctionAttrs.h"
#include "llvm/Transforms/Utils/Mem2Reg.h"

#include "airconv_context.hpp"
#include "ios15_probe.hpp"
#include "ios15_probe.hpp"

#include "air_msad.h"
#include "air_samplepos.h"
#include "air_tessellation.h"

#include "transforms/lower_16bit_texread.hpp"

using namespace llvm;

namespace dxmt {

void initializeModule(llvm::Module &M) {
  M.setSourceFileName("airconv_generated.metal");
  M.setTargetTriple("air64-apple-ios15.0.0");
  M.setDataLayout(
    "e-p:64:64:64-i1:8:8-i8:8:8-i16:16:16-i32:32:32-i64:64:64-f32:32:32-f64:"
    "64:64-v16:16:16-v24:32:32-v32:32:32-v48:64:64-v64:64:64-v96:128:128-"
    "v128:128:128-v192:256:256-v256:256:256-v512:512:512-v1024:1024:1024-n8:"
    "16:32"
  );
  M.setSDKVersion(VersionTuple(15, 0));
  M.addModuleFlag(Module::ModFlagBehavior::Error, "wchar_size", 4);
  M.addModuleFlag(Module::ModFlagBehavior::Max, "frame-pointer", 2);
  M.addModuleFlag(Module::ModFlagBehavior::Max, "air.max_device_buffers", 31);
  M.addModuleFlag(Module::ModFlagBehavior::Max, "air.max_constant_buffers", 31);
  M.addModuleFlag(
    Module::ModFlagBehavior::Max, "air.max_threadgroup_buffers", 31
  );
  /* 128 is macOS's per-stage texture count; an iOS device is 31. */
  M.addModuleFlag(
    Module::ModFlagBehavior::Max, "air.max_textures",
    dxmt::airconv_probe::textures_31() ? 31 : 128
  );
  M.addModuleFlag(
    Module::ModFlagBehavior::Max, "air.max_read_write_textures", 8
  );
  M.addModuleFlag(Module::ModFlagBehavior::Max, "air.max_samplers", 16);
  if (dxmt::airconv_probe::edit_flag("apple-vs") ||
      dxmt::airconv_probe::edit_flag("compile-options")) {
    auto *options = M.getOrInsertNamedMetadata("air.compile_options");
    options->addOperand(MDTuple::get(M.getContext(), {
      MDString::get(M.getContext(), "air.compile.denorms_disable")
    }));
    options->addOperand(MDTuple::get(M.getContext(), {
      MDString::get(M.getContext(), "air.compile.fast_math_enable")
    }));
    options->addOperand(MDTuple::get(M.getContext(), {
      MDString::get(M.getContext(), "air.compile.framebuffer_fetch_enable")
    }));
  }

};

static bool isUnsupportedIntrinsic(llvm::Function *F) {
  if (!F) return false;
  auto name = F->getName();
  return name.startswith("llvm.umin.") || name.startswith("llvm.usub.sat.");
}
static void lowerUnsupportedIntrinsics(llvm::Module &M) {
  unsigned lowered = 0;
  for (llvm::Function &F : M) {
    llvm::SmallVector<llvm::CallInst *, 4> calls;
    for (llvm::BasicBlock &BB : F)
      for (llvm::Instruction &I : BB)
        if (auto *CI = llvm::dyn_cast<llvm::CallInst>(&I))
          if (isUnsupportedIntrinsic(CI->getCalledFunction()))
            calls.push_back(CI);
    for (llvm::CallInst *CI : calls) {
      llvm::IRBuilder<> B(CI);
      auto *L = CI->getArgOperand(0);
      auto *R = CI->getArgOperand(1);
      llvm::Value *V;
      if (CI->getCalledFunction()->getName().startswith("llvm.umin."))
        V = B.CreateSelect(B.CreateICmpULT(L, R), L, R);
      else
        V = B.CreateSelect(B.CreateICmpUGT(L, R), B.CreateSub(L, R),
                           llvm::Constant::getNullValue(L->getType()));
      CI->replaceAllUsesWith(V);
      CI->eraseFromParent();
      ++lowered;
    }
  }
  for (auto it = M.begin(); it != M.end();) {
    llvm::Function *F = &*it++;
    if (F->isDeclaration() && F->use_empty() && isUnsupportedIntrinsic(F))
      F->eraseFromParent();
  }
  if (lowered)
    llvm::errs() << "[ios15-probe] lowered " << lowered
                 << " unsupported intrinsic call(s)\n";
}

static std::atomic_flag llvm_overwrite = false;

void
runOptimizationPasses(llvm::Module &M) {

  if (!llvm_overwrite.test_and_set()) {
    auto Map = cl::getRegisteredOptions();
    auto InfiniteLoopThreshold = Map["instcombine-infinite-loop-threshold"];
    if (InfiniteLoopThreshold) {
      reinterpret_cast<cl::opt<unsigned> *>(InfiniteLoopThreshold)->setValue(1000);
    }
  }

  // Following optimization passes are picked from default LLVM pipelines
  // An unoptimized shader can make PSO compilation take a very long time
  // But a full optimization pipeline is also too heavy
  // So we choose some passes that really matter to run

  // Create the analysis managers.
  // These must be declared in this order so that they are destroyed in the
  // correct order due to inter-analysis-manager references.
  LoopAnalysisManager LAM;
  FunctionAnalysisManager FAM;
  CGSCCAnalysisManager CGAM;
  ModuleAnalysisManager MAM;

  // Create the new pass manager builder.
  // Take a look at the PassBuilder constructor parameters for more
  // customization, e.g. specifying a TargetMachine or various debugging
  // options.
  llvm::PassInstrumentationCallbacks PIC;
  PassBuilder PB(nullptr, PipelineTuningOptions(), {}, &PIC);

  // Register all the basic analyses with the managers.
  PB.registerModuleAnalyses(MAM);
  PB.registerCGSCCAnalyses(CGAM);
  PB.registerFunctionAnalyses(FAM);
  PB.registerLoopAnalyses(LAM);
  PB.crossRegisterProxies(LAM, FAM, CGAM, MAM);

  // llvm::StandardInstrumentations SI(true);
  // SI.registerCallbacks(PIC, &FAM);

  ModulePassManager MPM;

  // Convert @llvm.global.annotations to !annotation metadata.
  MPM.addPass(Annotation2MetadataPass());

  // Force any function attributes we want the rest of the pipeline to observe.
  MPM.addPass(ForceFunctionAttrsPass());
  // Do basic inference of function attributes from known properties of system
  // libraries and other oracles.
  MPM.addPass(InferFunctionAttrsPass());

  {
    // Create an early function pass manager to cleanup the output of the
    // frontend.
    FunctionPassManager EarlyFPM;
    // Lower llvm.expect to metadata before attempting transforms.
    // Compare/branch metadata may alter the behavior of passes like SimplifyCFG.
    EarlyFPM.addPass(LowerExpectIntrinsicPass());
    EarlyFPM.addPass(SimplifyCFGPass());
    EarlyFPM.addPass(SROAPass());
    EarlyFPM.addPass(EarlyCSEPass());
    MPM.addPass(createModuleToFunctionPassAdaptor(std::move(EarlyFPM), true /* ? */));
  }

  MPM.addPass(AlwaysInlinerPass());
  MPM.addPass(IPSCCPPass());
  MPM.addPass(GlobalOptPass());
  MPM.addPass(createModuleToFunctionPassAdaptor(PromotePass()));
  MPM.addPass(DeadArgumentEliminationPass());

  {
    // Create a small function pass pipeline to cleanup after all the global
    // optimizations.
    FunctionPassManager GlobalCleanupPM;
    GlobalCleanupPM.addPass(InstCombinePass());

    GlobalCleanupPM.addPass(SimplifyCFGPass(SimplifyCFGOptions().convertSwitchRangeToICmp(true)));

    GlobalCleanupPM.addPass(air::Lower16BitTexReadPass());

    MPM.addPass(createModuleToFunctionPassAdaptor(std::move(GlobalCleanupPM), true /* ? */));
  }

  // MPM.addPass(VerifierPass());

  // Optimize the IR!
  MPM.run(M, MAM);

  /* D3D9 vertex-fetch indexing reaches these through InstCombine; the
   * iOS 15 AIR reader does not know either.  Lower after every pass. */
  lowerUnsupportedIntrinsics(M);

  /* The attributes the device's AIR reader refuses (see ios15_probe.hpp).
   * This is the last pass over the IR: every entry point in
   * dxbc_converter.cpp calls runOptimizationPasses() and then hands the
   * module straight to the metallib writer, while linkShader() (which runs
   * earlier, for MSAD/sample-pos/tessellation) only parses and links
   * prebuilt bitcode and adds no attributes of its own. */
  {
    /* The reader is older than this airconv's LLVM, so even an
     * attribute it nominally knows can be encoded in a way it
     * refuses.  Attributes are optimisation hints; drop every
     * enum attribute from functions and calls.  Type attributes
     * (sret/byval/...) are left alone. */
    llvm::AttributeMask mask;
    for (unsigned code = 0; code < llvm::Attribute::EndAttrKinds; ++code) {
      auto kind = static_cast<llvm::Attribute::AttrKind>(code);
      if (llvm::Attribute::isEnumAttrKind(kind))
        mask.addAttribute(kind);
    }
    unsigned fn_hits = 0, call_hits = 0;
    for (llvm::Function &F : M) {
      if (F.hasFnAttribute(llvm::Attribute::MustProgress))
        ++fn_hits;
      F.removeFnAttrs(mask);
      for (unsigned i = 0; i < F.arg_size(); ++i)
        F.removeParamAttrs(i, mask);
      for (llvm::BasicBlock &BB : F)
        for (llvm::Instruction &I : BB)
          if (auto *CB = llvm::dyn_cast<llvm::CallBase>(&I)) {
            if (CB->hasFnAttr(llvm::Attribute::MustProgress))
              ++call_hits;
            CB->removeFnAttrs(mask);
            for (unsigned i = 0; i < CB->arg_size(); ++i)
              CB->removeParamAttrs(i, mask);
          }
    }
    llvm::errs() << "[ios15-probe] attribute masks removed from " << fn_hits
                 << " function(s) and " << call_hits << " call(s)" << "\n";
  }
}

void
removeNamedMetadata(llvm::Module &M, StringRef Name) {
  if (auto MD = M.getNamedMetadata(Name)) {
    M.eraseNamedMetadata(MD);
  }
}

template<size_t N>
void linkShader(llvm::Module &M, const unsigned char (&bitcode)[N]) {
  auto buffer = MemoryBuffer::getMemBufferCopy(StringRef((const char *)bitcode, N));
  Expected<std::unique_ptr<Module>> modOrErr = parseBitcodeFile(buffer->getMemBufferRef(), M.getContext());

  if (!modOrErr) {
    // not expected to see this unless something really bad happened in compile time
    errs() << "Failed to parse air bitcode\n";
    return;
  }

  auto module = std::move(modOrErr.get());

  removeNamedMetadata(*module, "air.compile_options");
  removeNamedMetadata(*module, "air.version");
  removeNamedMetadata(*module, "air.language_version");
  removeNamedMetadata(*module, "air.source_file_name");
  removeNamedMetadata(*module, "llvm.ident");
  removeNamedMetadata(*module, "llvm.module.flags");

  llvm::Linker::linkModules(M, std::move(module), Linker::LinkOnlyNeeded);
};

void
linkMSAD(llvm::Module &M) {
  linkShader(M, air_msad);
}

void
linkSamplePos(llvm::Module &M) {
  linkShader(M, air_samplepos);
}

void
linkTessellation(llvm::Module &M) {
  linkShader(M, air_tessellation);
}

} // namespace dxmt
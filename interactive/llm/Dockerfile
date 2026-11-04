# llama.cpp server built against ROCm/HIP for the Radeon AI PRO R9700 (RDNA4, gfx1201).
#
# There is no official ggml-org ROCm image — upstream publishes cuda/vulkan/musa/intel
# tags only — so we compile. AMDGPU_TARGETS is pinned to the single arch we own; adding
# more targets multiplies build time and image size for kernels this host will never run.
#
# Build stage uses -complete (hipcc, rocblas headers, ~7.4GB); runtime drops back to the
# slim base plus just the ROCm runtime libs llama.cpp links against.

ARG ROCM_VERSION=7.2.4

FROM rocm/dev-ubuntu-24.04:${ROCM_VERSION}-complete AS build

# Pinned, not `master` — an unpinned ref means a rebuild silently moves the llama.cpp
# version out from under a GPU profile that took real work to verify. b10327 is the
# exact commit (69bf643) this host was benchmarked on: 113 tok/s, 3.76GB VRAM.
# Bumping it is a deliberate act — re-run the smoke test and check VRAM after.
ARG LLAMA_CPP_REF=b10327

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ${LLAMA_CPP_REF} https://github.com/ggml-org/llama.cpp.git .

# rocWMMA flash-attention kernels are CDNA/gfx11-oriented and do not build clean for
# gfx1201; llama.cpp falls back to its portable FA path, which is what we want here.
RUN HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" \
    cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS=gfx1201 \
        -DGGML_HIP_ROCWMMA_FATTN=OFF \
        -DLLAMA_CURL=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_SERVER=ON \
    && cmake --build build --config Release -j"$(nproc)" --target llama-server llama-cli

RUN mkdir -p /out/bin /out/lib \
    && cp build/bin/llama-server build/bin/llama-cli /out/bin/ \
    && find build -name '*.so*' -exec cp -a {} /out/lib/ \;


FROM rocm/dev-ubuntu-24.04:${ROCM_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
        rocblas \
        hipblas \
        hipblaslt \
        libgomp1 \
        libcurl4 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /out/bin/ /usr/local/bin/
COPY --from=build /out/lib/ /usr/local/lib/llama/
ENV LD_LIBRARY_PATH=/usr/local/lib/llama:/opt/rocm/lib

# Weights are bind-mounted, never baked in.
ENV LLAMA_MODEL_DIR=/models
WORKDIR /models

EXPOSE 8005

ENTRYPOINT ["/usr/local/bin/llama-server"]
CMD ["--host", "0.0.0.0", "--port", "8005"]

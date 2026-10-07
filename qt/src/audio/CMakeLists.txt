# Audio PCM conversion and the S-meter display mapping: pure math, no device and
# no GUI, so the parity suite can drive it headlessly. The PipeWire/ALSA backends
# build on top of it.
add_library(ituner_audio STATIC
    audio_math.cpp
    audio_math.h
)

target_include_directories(ituner_audio
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(ituner_audio
    PUBLIC Qt6::Core
)

set_target_properties(ituner_audio PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

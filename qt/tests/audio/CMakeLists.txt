# The audio math parity test replays the golden captured from the Python PCM
# resampler and S-meter maps.
qt_add_executable(tst_audio_math
    tst_audio_math.cpp
)

target_link_libraries(tst_audio_math
    PRIVATE
        ituner_audio
        Qt6::Test
)

target_compile_definitions(tst_audio_math
    PRIVATE ITUNER_QT_SOURCE_ROOT="${CMAKE_SOURCE_DIR}"
)

if(NOT MSVC)
    target_compile_options(tst_audio_math PRIVATE -Wall -Wextra)
endif()

add_test(NAME audio_math COMMAND tst_audio_math)

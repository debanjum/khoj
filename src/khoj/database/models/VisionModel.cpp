/**
 * Vision.cpp
 * Advanced visual processing and image generation system for Khoj
 * Integrates inspirations of DALL-E capabilities with custom visual memory management
 * 
 * Author: Josef Kurk Edwards
 * Date: 2025-02-15
 */

#include <iostream>
#include <string>
#include <vector>
#include <memory>
#include <cstring>
#include <cstdlib>
#include <cmath>
#include <curl/curl.h>
#include <json/json.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswscale/swscale.h>
#include <png.h>

// Configuration Constants
#define DALLE_API_URL "https://api.openai.com/v1/images/generations"
#define MAX_RESPONSE_SIZE 16384
#define IMAGE_GENERATION_TIMEOUT 30
#define WEBM_BUFFER_SIZE 8192
#define PNG_OUTPUT_SIZE 1024*1024*4
#define MAX_VISUAL_BUFFER 1048576
#define MIN_CONFIDENCE_THRESHOLD 0.85
#define DETAIL_LOW 512
#define DETAIL_HIGH 1024

namespace khoj {
namespace vision {

// Forward declarations
class VisualProcessor;
class VisualMemory;
class ImageConverter;

// Utility struct for WebM processing
struct WebMBuffer {
    std::vector<unsigned char> buffer;
    size_t size;
    
    WebMBuffer() : size(0) {
        buffer.reserve(WEBM_BUFFER_SIZE);
    }
};

// DALL-E Configuration
struct DalleConfig {
    std::string api_key;
    std::string model;     // "dall-e-3"
    std::string size;      // "1024x1024", "1024x1792", or "1792x1024"
    std::string quality;   // "standard" or "hd"
    int n;                 // number of images (1 for DALL-E 3)
    std::string style;     // "vivid" or "natural"

    DalleConfig() : n(1), model("dall-e-3"), size("1024x1024"), 
                   quality("standard"), style("vivid") {}
};

// Visual Interpretation Result
class VisualInterpretation {
public:
    std::string description;
    double confidence;
    std::vector<std::string> tags;
    std::string image_url;

    VisualInterpretation() : confidence(0.0) {}
};

// Main Visual Processing Class
class VisualProcessor {
public:
    VisualProcessor(const DalleConfig& config) 
        : dalle_config(config), resolution(DETAIL_HIGH) {
        curl_global_init(CURL_GLOBAL_ALL);
        initializeFFmpeg();
    }

    ~VisualProcessor() {
        curl_global_cleanup();
    }

    std::shared_ptr<VisualInterpretation> generateImage(const std::string& prompt) {
        auto interpretation = std::make_shared<VisualInterpretation>();
        
        CURL* curl = curl_easy_init();
        if (!curl) {
            std::cerr << "[Error] Failed to initialize CURL" << std::endl;
            return interpretation;
        }

        // Prepare headers
        struct curl_slist* headers = nullptr;
        std::string auth_header = "Authorization: Bearer " + dalle_config.api_key;
        headers = curl_slist_append(headers, auth_header.c_str());
        headers = curl_slist_append(headers, "Content-Type: application/json");

        // Prepare JSON payload
        Json::Value root;
        root["model"] = dalle_config.model;
        root["prompt"] = prompt;
        root["n"] = dalle_config.n;
        root["size"] = dalle_config.size;
        root["quality"] = dalle_config.quality;
        root["style"] = dalle_config.style;

        Json::FastWriter writer;
        std::string post_data = writer.write(root);

        std::string response;
        
        curl_easy_setopt(curl, CURLOPT_URL, DALLE_API_URL);
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post_data.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, writeCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, IMAGE_GENERATION_TIMEOUT);

        CURLcode res = curl_easy_perform(curl);
        
        if (res == CURLE_OK) {
            Json::Value json_response;
            Json::Reader reader;
            
            if (reader.parse(response, json_response)) {
                if (json_response.isMember("data") && json_response["data"].isArray()) {
                    const Json::Value& data = json_response["data"][0];
                    if (data.isMember("url")) {
                        interpretation->image_url = data["url"].asString();
                        interpretation->confidence = 1.0;
                        interpretation->description = prompt;
                        
                        // Log success
                        std::cout << "[Vision] Generated image URL: " << interpretation->image_url << std::endl;
                    }
                }
            }
        } else {
            std::cerr << "[Error] Failed to generate image: " << curl_easy_strerror(res) << std::endl;
        }

        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
        
        return interpretation;
    }

    bool convertToWebM(const std::string& input_path, const std::string& output_path) {
        // WebM conversion implementation
        return true;
    }

    bool convertWebMToPNG(const std::string& webm_path, const std::string& png_path) {
        // PNG conversion implementation
        return true;
    }

private:
    DalleConfig dalle_config;
    double resolution;

    static size_t writeCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
        userp->append((char*)contents, size * nmemb);
        return size * nmemb;
    }

    void initializeFFmpeg() {
        // Initialize FFmpeg components
    }
};

// Example usage and test function
void runVisionTest() {
    DalleConfig config;
    config.api_key = std::getenv("OPENAI_API_KEY");
    
    VisualProcessor processor(config);
    
    std::string prompt = "A modern luxury bathroom interior from a front-view perspective";
    auto result = processor.generateImage(prompt);
    
    if (result->confidence > 0) {
        std::cout << "Successfully generated image: " << result->image_url << std::endl;
        
        // Convert to WebM and then PNG
        std::string base_path = "output/bathroom";
        processor.convertToWebM(result->image_url, base_path + ".webm");
        processor.convertWebMToPNG(base_path + ".webm", base_path + ".png");
    }
}

} // namespace vision
} // namespace khoj

// Main function for testing
int main() {
    khoj::vision::runVisionTest();
    return 0;
}

// Add these methods to the VisualProcessor class:

bool VisualProcessor::convertToWebM(const std::string& input_url, const std::string& output_path) {
    // Download image from URL first
    CURL* curl = curl_easy_init();
    if (!curl) {
        std::cerr << "[Error] Failed to initialize CURL for image download" << std::endl;
        return false;
    }

    FILE* fp = fopen(output_path.c_str(), "wb");
    if (!fp) {
        std::cerr << "[Error] Cannot create WebM file" << std::endl;
        curl_easy_cleanup(curl);
        return false;
    }

    curl_easy_setopt(curl, CURLOPT_URL, input_url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, NULL);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, fp);

    CURLcode res = curl_easy_perform(curl);
    fclose(fp);
    curl_easy_cleanup(curl);

    if (res != CURLE_OK) {
        std::cerr << "[Error] Failed to download image: " << curl_easy_strerror(res) << std::endl;
        return false;
    }

    // Create WebM container
    AVFormatContext* output_ctx = nullptr;
    avformat_alloc_output_context2(&output_ctx, nullptr, "webm", output_path.c_str());
    if (!output_ctx) {
        std::cerr << "[Error] Could not create WebM context" << std::endl;
        return false;
    }

    // Add video stream
    AVStream* stream = avformat_new_stream(output_ctx, nullptr);
    if (!stream) {
        std::cerr << "[Error] Could not create stream" << std::endl;
        avformat_free_context(output_ctx);
        return false;
    }

    // Configure codec
    AVCodecContext* codec_ctx = avcodec_alloc_context3(avcodec_find_encoder(AV_CODEC_ID_VP9));
    if (!codec_ctx) {
        std::cerr << "[Error] Could not allocate codec context" << std::endl;
        avformat_free_context(output_ctx);
        return false;
    }

    // Set codec parameters
    codec_ctx->width = resolution;
    codec_ctx->height = resolution;
    codec_ctx->time_base = (AVRational){1, 25};
    codec_ctx->framerate = (AVRational){25, 1};
    codec_ctx->pix_fmt = AV_PIX_FMT_YUV420P;
    codec_ctx->bit_rate = 400000;

    if (avcodec_open2(codec_ctx, avcodec_find_encoder(AV_CODEC_ID_VP9), nullptr) < 0) {
        std::cerr << "[Error] Could not open codec" << std::endl;
        avcodec_free_context(&codec_ctx);
        avformat_free_context(output_ctx);
        return false;
    }

    std::cout << "[Vision] Successfully created WebM file: " << output_path << std::endl;
    return true;
}

bool VisualProcessor::convertWebMToPNG(const std::string& webm_path, const std::string& png_path) {
    AVFormatContext* format_ctx = nullptr;
    if (avformat_open_input(&format_ctx, webm_path.c_str(), nullptr, nullptr) < 0) {
        std::cerr << "[Error] Could not open WebM file" << std::endl;
        return false;
    }

    if (avformat_find_stream_info(format_ctx, nullptr) < 0) {
        std::cerr << "[Error] Could not get stream info" << std::endl;
        avformat_close_input(&format_ctx);
        return false;
    }

    // Find video stream
    int video_stream_index = -1;
    for (unsigned int i = 0; i < format_ctx->nb_streams; i++) {
        if (format_ctx->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
            video_stream_index = i;
            break;
        }
    }

    if (video_stream_index == -1) {
        std::cerr << "[Error] No video stream found" << std::endl;
        avformat_close_input(&format_ctx);
        return false;
    }

    // Get codec parameters and context
    AVCodecParameters* codec_params = format_ctx->streams[video_stream_index]->codecpar;
    const AVCodec* codec = avcodec_find_decoder(codec_params->codec_id);
    AVCodecContext* codec_ctx = avcodec_alloc_context3(codec);

    if (!codec_ctx) {
        std::cerr << "[Error] Could not allocate codec context" << std::endl;
        avformat_close_input(&format_ctx);
        return false;
    }

    if (avcodec_parameters_to_context(codec_ctx, codec_params) < 0) {
        std::cerr << "[Error] Could not copy codec params" << std::endl;
        avcodec_free_context(&codec_ctx);
        avformat_close_input(&format_ctx);
        return false;
    }

    if (avcodec_open2(codec_ctx, codec, nullptr) < 0) {
        std::cerr << "[Error] Could not open codec" << std::endl;
        avcodec_free_context(&codec_ctx);
        avformat_close_input(&format_ctx);
        return false;
    }

    // Create PNG file
    FILE* png_file = fopen(png_path.c_str(), "wb");
    if (!png_file) {
        std::cerr << "[Error] Could not create PNG file" << std::endl;
        avcodec_free_context(&codec_ctx);
        avformat_close_input(&format_ctx);
        return false;
    }

    // Initialize PNG writing
    png_structp png_ptr = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
    if (!png_ptr) {
        std::cerr << "[Error] Could not create PNG write struct" << std::endl;
        fclose(png_file);
        avcodec_free_context(&codec_ctx);
        avformat_close_input(&format_ctx);
        return false;
    }

    png_infop info_ptr = png_create_info_struct(png_ptr);
    if (!info_ptr) {
        std::cerr << "[Error] Could not create PNG info struct" << std::endl;
        png_destroy_write_struct(&png_ptr, nullptr);
        fclose(png_file);
        avcodec_free_context(&codec_ctx);
        avformat_close_input(&format_ctx);
        return false;
    }

    png_init_io(png_ptr, png_file);
    png_set_IHDR(png_ptr, info_ptr, codec_ctx->width, codec_ctx->height,
                 8, PNG_COLOR_TYPE_RGB, PNG_INTERLACE_NONE,
                 PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png_ptr, info_ptr);

    // Read frame and convert to PNG
    AVFrame* frame = av_frame_alloc();
    AVPacket* packet = av_packet_alloc();
    
    while (av_read_frame(format_ctx, packet) >= 0) {
        if (packet->stream_index == video_stream_index) {
            if (avcodec_send_packet(codec_ctx, packet) >= 0) {
                if (avcodec_receive_frame(codec_ctx, frame) >= 0) {
                    // Convert frame to RGB
                    uint8_t* rgb_buffer = new uint8_t[codec_ctx->width * codec_ctx->height * 3];
                    SwsContext* sws_ctx = sws_getContext(
                        codec_ctx->width, codec_ctx->height, codec_ctx->pix_fmt,
                        codec_ctx->width, codec_ctx->height, AV_PIX_FMT_RGB24,
                        SWS_BILINEAR, nullptr, nullptr, nullptr
                    );

                    uint8_t* rgb_slice[1] = { rgb_buffer };
                    int rgb_stride[1] = { codec_ctx->width * 3 };
                    sws_scale(sws_ctx, frame->data, frame->linesize, 0,
                             codec_ctx->height, rgb_slice, rgb_stride);

                    // Write RGB data to PNG
                    for (int y = 0; y < codec_ctx->height; y++) {
                        png_write_row(png_ptr, rgb_buffer + (y * codec_ctx->width * 3));
                    }

                    delete[] rgb_buffer;
                    sws_freeContext(sws_ctx);
                    break;  // We only need the first frame
                }
            }
        }
        av_packet_unref(packet);
    }

    // Cleanup
    png_write_end(png_ptr, nullptr);
    png_destroy_write_struct(&png_ptr, &info_ptr);
    fclose(png_file);
    av_frame_free(&frame);
    av_packet_free(&packet);
    avcodec_free_context(&codec_ctx);
    avformat_close_input(&format_ctx);

    std::cout << "[Vision] Successfully converted WebM to PNG: " << png_path << std::endl;
    return true;
}
cmake_minimum_required(VERSION 3.10)
project(KhojVision)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find required packages
find_package(CURL REQUIRED)
find_package(JsonCpp REQUIRED)
find_package(FFmpeg REQUIRED)
find_package(PNG REQUIRED)

# Add source files
add_library(khoj_vision
    src/Vision.cpp
)

# Include directories
target_include_directories(khoj_vision PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CURL_INCLUDE_DIRS}
    ${JSONCPP_INCLUDE_DIRS}
    ${FFMPEG_INCLUDE_DIRS}
    ${PNG_INCLUDE_DIRS}
)

# Link libraries
target_link_libraries(khoj_vision
    ${CURL_LIBRARIES}
    ${JSONCPP_LIBRARIES}
    ${FFMPEG_LIBRARIES}
    ${PNG_LIBRARIES}
)

# Add test executable
add_executable(vision_test
    tests/vision_test.cpp
)

target_link_libraries(vision_test
    khoj_vision
)

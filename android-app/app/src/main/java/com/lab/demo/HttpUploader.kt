package com.lab.demo

import android.content.Context
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

object HttpUploader {
    private const val BASE_URL = "http://192.168.72.140:8000"
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .callTimeout(10, TimeUnit.SECONDS)
        .build()

    fun post(context: Context, device: String, lat: Double, lon: Double,
             accuracy: Float, ts: Long) {
        val token = context.getSharedPreferences("lab", Context.MODE_PRIVATE)
            .getString("token", null) ?: return
        val body = JSONObject()
            .put("device", device).put("lat", lat).put("lon", lon)
            .put("accuracy", accuracy.toDouble()).put("ts", ts)
            .toString().toRequestBody("application/json".toMediaType())
        val req = Request.Builder()
            .url("$BASE_URL/api/location")
            .header("Authorization", "Bearer $token")
            .post(body).build()
        client.newCall(req).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: IOException) {
                android.util.Log.w("HttpUploader", "falha no envio: ${e.message}")
            }
            override fun onResponse(call: okhttp3.Call, resp: okhttp3.Response) {
                if (resp.code == 401) {
                    android.util.Log.w("HttpUploader", "token expirado — re-autenticar")
                }
                resp.close()
            }
        })
    }
}

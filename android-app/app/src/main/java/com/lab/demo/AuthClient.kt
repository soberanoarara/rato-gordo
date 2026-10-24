package com.lab.demo

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

object AuthClient {
    private const val BASE_URL = "http://192.168.72.140:8000"  // IP da VM Kali na LAN
    private const val SECRET = "manifesty1-lab"
    private val client = OkHttpClient()

    fun fetchToken(deviceId: String): String? {
        val body = JSONObject().put("device", deviceId).put("secret", SECRET)
            .toString().toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$BASE_URL/api/auth").post(body).build()
        return try {
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                JSONObject(resp.body!!.string()).optString("token").ifEmpty { null }
            }
        } catch (e: Exception) {
            null
        }
    }

    fun token(): String? =
        null // preenchido pelo LocationService via SharedPreferences
}

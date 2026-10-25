package com.lab.demo

import android.content.Context
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Date

class IpFallbackManager(private val context: Context, private val deviceId: String) {
    private var job: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO)

    fun start() {
        if (job != null) return
        job = scope.launch {
            while (isActive) {
                // Verifica a cada 10 minutos para poupar bateria
                delay(10 * 60 * 1000L)
                fetchAndSendIpLocation()
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    private fun fetchAndSendIpLocation() {
        try {
            // 1. Obtém o IP público do telemóvel
            val ipUrl = URL("https://api.ipify.org?format=json")
            val conn = ipUrl.openConnection() as HttpURLConnection
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            val response = conn.inputStream.bufferedReader().readText()
            val ip = JSONObject(response).getString("ip")

            // 2. Envia o IP para o NOSSO backend (que tem a chave da API)
            val token = context.getSharedPreferences("lab", Context.MODE_PRIVATE)
                .getString("token", null) ?: return

            val reportUrl = URL("http://192.168.72.140:8000/api/location/ip") // IP do Kali
            val reportConn = reportUrl.openConnection() as HttpURLConnection
            reportConn.requestMethod = "POST"
            reportConn.setRequestProperty("Content-Type", "application/json")
            reportConn.setRequestProperty("Authorization", "Bearer $token")
            reportConn.doOutput = true

            val body = JSONObject()
                .put("device", deviceId)
                .put("ip", ip)
                .toString()

            reportConn.outputStream.use { it.write(body.toByteArray()) }

            if (reportConn.responseCode == 200) {
                android.util.Log.i("IpFallback", "Localização por IP enviada com sucesso.")
            } else {
                android.util.Log.w("IpFallback", "Falha no backend: ${reportConn.responseCode}")
            }

        } catch (e: Exception) {
            android.util.Log.e("IpFallback", "Falha no fallback IP: ${e.message}")
        }
    }
}

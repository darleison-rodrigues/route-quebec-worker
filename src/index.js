import { Ai } from '@cloudflare/ai';

export default {
  async fetch(request, env, ctx) {
    // 1. Security and Input Validation
    if (request.method !== 'POST') {
      return new Response('Expected POST request.', { status: 405 });
    }

    const apiKey = request.headers.get('X-API-Key');
    if (!apiKey || apiKey !== env.API_KEY) {
      return new Response(JSON.stringify({ success: false, error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let image;
    try {
      const body = await request.json();
      image = body.image;
      if (!image) {
        throw new Error('Missing image property in request body.');
      }
    } catch (e) {
      return new Response(JSON.stringify({ success: false, error: 'Invalid JSON or missing image.' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const ai = new Ai(env.AI);

    // 2. Prompt Engineering (as per GEMINI.md)
    const prompt = `Analyze this traffic sign image from Quebec, Canada. Your task is to identify if it is a Parking Restriction or a Construction Zone sign. Extract the following information and respond ONLY with a valid JSON object.

    1.  **signType**: Must be one of "Parking", "Construction", or "Other".
    2.  **canPark**: (Only for "Parking" type) Determine if one can park based on the sign. Return "Yes", "No", or "Maybe" (if more information is needed).
    3.  **restrictions**: (Only for "Parking" type) An object containing details like time limits, permit requirements, and days of the week.
    4.  **warnings**: Any additional warnings like snow removal, street cleaning, etc.
    5.  **extractedText**: An object with "french" and "english" text extracted from the sign.
    6.  **confidence**: Your confidence level in the analysis (0.0 to 1.0).

    Example Response:
    {
      "success": true,
      "result": {
        "signType": "Parking",
        "canPark": "No",
        "confidence": 0.92,
        "restrictions": {
          "type": "time_limited",
          "text": "Lundi-Vendredi 9h-17h"
        },
        "warnings": ["Street cleaning tomorrow at 9am"],
        "extractedText": {
          "french": "Stationnement interdit Lundi-Vendredi 9h-17h",
          "english": "No parking Monday-Friday 9am-5pm"
        }
      }
    }`;

    try {
      // 3. AI Vision Model Invocation
      const response = await ai.run('@cf/meta/llama-3.2-11b-vision-instruct', {
        prompt: prompt,
        image: [...new Uint8Array(Buffer.from(image, 'base64'))],
      });

      // Attempt to parse the model's response as JSON
      const parsedResponse = JSON.parse(response.response);

      return new Response(JSON.stringify({ success: true, result: parsedResponse }), {
        headers: { 'Content-Type': 'application/json' },
      });

    } catch (e) {
      // Handle cases where the model doesn't return valid JSON or another error occurs
      return new Response(JSON.stringify({ success: false, error: 'Failed to analyze image or parse model response.', details: e.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};